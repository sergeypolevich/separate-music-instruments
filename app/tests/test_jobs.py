from dataclasses import replace
from pathlib import Path

import numpy as np
import soundfile

from audio_stems.models import (
    AdapterError,
    BatchStatus,
    DownloadedAudio,
    FailureCategory,
    JobStatus,
    SeparationOptions,
    SeparationResult,
    SourceKind,
    SourceRequest,
    StemArtifact,
    VideoCandidate,
    disk_full_error,
)
from audio_stems.models import MediaAssetType
from audio_stems.pipeline import BatchOrchestrator, DatabaseWorker
from audio_stems.storage import LocalMediaStorage
from audio_stems.db import SqliteMetadataStore
from audio_stems.config import Settings


STEMS = ("vocals", "drums", "bass", "guitar", "piano", "other")


class FakeDownloader:
    def __init__(self, temp_path, events, *, permanent_failures=(), transient_failures=None):
        self.temp_path = temp_path
        self.events = events
        self.permanent_failures = set(permanent_failures)
        self.transient_failures = dict(transient_failures or {})
        self.attempts = {}

    def search(self, query):
        self.events.append(("search", query))
        source_id = f"id-{query}"
        return [VideoCandidate(source_id, source_id, f"https://youtube.com/watch?v={source_id}")]

    def download(self, source, progress=None):
        self.events.append(("download", source.source_id))
        attempts = self.attempts.get(source.source_id, 0) + 1
        self.attempts[source.source_id] = attempts
        if source.source_id in self.permanent_failures:
            raise AdapterError("provider unavailable", FailureCategory.PERMANENT)
        if attempts <= self.transient_failures.get(source.source_id, 0):
            raise AdapterError("temporary provider issue", FailureCategory.TRANSIENT)
        audio_path = self.temp_path / f"{source.source_id}.webm"
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"\x1aE\xdf\xa3audio-data")
        return DownloadedAudio(audio_path, source, "webm", audio_path.stat().st_size)


class FakeSeparator:
    def __init__(self, events, *, failures=(), incomplete=()):
        self.events = events
        self.failures = set(failures)
        self.incomplete = set(incomplete)
        self.calls = []
        self.options = []

    def model_stems(self, model_name):
        return ("vocals", "drums", "bass", "other") if model_name == "htdemucs_ft" else STEMS

    def separate(self, audio_path: Path, output_dir: Path, options: SeparationOptions, progress=None):
        track = audio_path.stem
        self.events.append(("separate", track))
        self.calls.append(track)
        self.options.append(options)
        if track in self.failures:
            raise AdapterError("model failed", FailureCategory.PERMANENT)
        output_dir.mkdir(parents=True, exist_ok=True)
        names = STEMS[:-1] if track in self.incomplete else STEMS
        artifacts = []
        for index, name in enumerate(names):
            path = output_dir / f"{name}.wav"
            soundfile.write(path, np.full((64, 2), 0.1 * (index + 1), np.float32), 44100, subtype="FLOAT")
            artifacts.append(StemArtifact(name, path))
        return SeparationResult(tuple(artifacts))


def build_orchestrator(tmp_path, *, downloader=None, separator=None, delay_log=None, retry_delays=(0.0, 0.0)):
    events = []
    store = SqliteMetadataStore(tmp_path / "audio_stems.db.db")
    storage = LocalMediaStorage(tmp_path / "media")
    downloader = downloader or FakeDownloader(tmp_path / "downloads", events)
    separator = separator or FakeSeparator(events)
    settings = replace(
        Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"),
        retry_count=3,
        retry_delays=retry_delays,
    )
    orchestrator = BatchOrchestrator(
        repository=store,
        downloader=downloader,
        separator=separator,
        storage=storage,
        options=SeparationOptions("htdemucs_6s", "cpu"),
        settings=settings,
        sleep=(delay_log or (lambda _delay: None)),
    )
    return orchestrator, store, storage, events, downloader, separator


def test_batch_finishes_every_download_before_separation_and_reports_partial_success(tmp_path):
    events = []
    downloader = FakeDownloader(tmp_path / "downloads", events, permanent_failures={"id-failed"})
    separator = FakeSeparator(events)
    orchestrator, store, _, _, _, _ = build_orchestrator(
        tmp_path, downloader=downloader, separator=separator
    )
    requests = [
        SourceRequest(SourceKind.SEARCH_QUERY, "failed"),
        SourceRequest(SourceKind.SEARCH_QUERY, "successful"),
    ]
    receipt = store.create_batch("client-1", requests)

    result = orchestrator.process_batch(receipt, requests)

    event_kinds = [event[0] for event in events]
    assert max(i for i, kind in enumerate(event_kinds) if kind == "download") < min(
        i for i, kind in enumerate(event_kinds) if kind == "separate"
    )
    assert [job.status for job in result.jobs] == [JobStatus.FAILED, JobStatus.COMPLETED]
    assert result.status is BatchStatus.PARTIAL
    assert store.get_batch(receipt.batch_id, "client-1").status == BatchStatus.PARTIAL.value
    assert result.jobs[0].error_summary == "Processing failed permanently"
    assert downloader.attempts["id-failed"] == 1
    assert result.jobs[0].retry_count == 0


def test_transient_errors_retry_and_persist_attempt_count(tmp_path):
    events = []
    downloader = FakeDownloader(tmp_path / "downloads", events, transient_failures={"id-track": 2})
    retries = []
    orchestrator, store, _, _, _, _ = build_orchestrator(
        tmp_path, downloader=downloader, delay_log=retries.append
    )
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "track")]
    receipt = store.create_batch("client-1", requests)

    result = orchestrator.process_batch(receipt, requests)

    assert downloader.attempts["id-track"] == 3
    assert retries == [0.0, 0.0]
    assert result.jobs[0].retry_count == 2
    assert result.jobs[0].status is JobStatus.COMPLETED


def test_permanent_separation_failure_does_not_stop_next_job(tmp_path):
    events = []
    downloader = FakeDownloader(tmp_path / "downloads", events)
    separator = FakeSeparator(events, failures={"id-first"})
    orchestrator, store, _, _, _, _ = build_orchestrator(
        tmp_path, downloader=downloader, separator=separator
    )
    requests = [
        SourceRequest(SourceKind.SEARCH_QUERY, "first"),
        SourceRequest(SourceKind.SEARCH_QUERY, "second"),
    ]
    receipt = store.create_batch("client-1", requests)

    result = orchestrator.process_batch(receipt, requests)

    assert [job.status for job in result.jobs] == [JobStatus.FAILED, JobStatus.COMPLETED]
    assert separator.calls == ["id-first", "id-second"]
    assert result.status is BatchStatus.PARTIAL


def test_incomplete_separation_fails_and_keeps_temp_outputs(tmp_path):
    events = []
    downloader = FakeDownloader(tmp_path / "downloads", events)
    separator = FakeSeparator(events, incomplete={"id-track"})
    orchestrator, store, storage, _, _, _ = build_orchestrator(
        tmp_path, downloader=downloader, separator=separator
    )
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "track")]
    receipt = store.create_batch("client-1", requests)

    result = orchestrator.process_batch(receipt, requests)

    assert result.jobs[0].status is JobStatus.FAILED
    assert storage.temporary_directory("id-track").exists()
    assert result.status is BatchStatus.FAILED


def test_complete_existing_stems_are_reused_without_separator(tmp_path):
    events = []
    downloader = FakeDownloader(tmp_path / "downloads", events)
    separator = FakeSeparator(events)
    orchestrator, store, storage, _, _, _ = build_orchestrator(
        tmp_path, downloader=downloader, separator=separator
    )
    source_id = "id-track"
    for name in STEMS:
        target = storage.stem_file_target("id-track", name)
        target.path.parent.mkdir(parents=True, exist_ok=True)
        target.path.write_bytes(b"existing-stem")
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "track")]
    receipt = store.create_batch("client-1", requests)

    result = orchestrator.process_batch(receipt, requests)

    assert result.jobs[0].status is JobStatus.COMPLETED
    assert result.status is BatchStatus.COMPLETED
    assert separator.calls == []
    assert len(store.list_assets(receipt.job_ids[0], "client-1")) == 7


def test_existing_audio_for_same_source_skips_downloader(tmp_path):
    events = []
    downloader = FakeDownloader(tmp_path / "downloads", events)
    separator = FakeSeparator(events)
    orchestrator, store, storage, _, _, _ = build_orchestrator(
        tmp_path, downloader=downloader, separator=separator
    )
    source = VideoCandidate("id-track", "id-track", "https://youtube.com/watch?v=id-track")
    target = storage.audio_target(source.title, "webm", source.source_id)
    target.path.write_bytes(b"\x1aE\xdf\xa3cached-audio")
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "track")]
    receipt = store.create_batch("client-1", requests)

    result = orchestrator.process_batch(receipt, requests)

    assert result.jobs[0].status is JobStatus.COMPLETED
    assert not any(kind == "download" for kind, _value in events)


def test_batch_specific_model_options_are_persisted_and_passed_to_separator(tmp_path):
    orchestrator, store, _, _, _, separator = build_orchestrator(tmp_path)
    selected = SeparationOptions("htdemucs", "cpu")
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "Track")]
    receipt = store.create_batch("owner-1", requests, separation_options=selected)  # as the API does

    orchestrator.process_batch(receipt, requests)

    assert store.get_job(receipt.job_ids[0], "owner-1").separation_options == selected
    assert separator.options == [selected]


def test_direct_url_uses_the_exact_video_without_search(tmp_path):
    orchestrator, store, _, events, _, _ = build_orchestrator(tmp_path)
    url = "https://www.youtube.com/watch?v=exact-id"
    requests = [SourceRequest(SourceKind.VIDEO_URL, url)]
    receipt = store.create_batch("client-1", requests)

    result = orchestrator.process_batch(receipt, requests)

    assert not [event for event in events if event[0] == "search"]
    assert ("download", "exact-id") in events
    assert result.jobs[0].status is JobStatus.COMPLETED


def test_successful_separation_removes_temporary_outputs(tmp_path):
    orchestrator, store, storage, _, _, _ = build_orchestrator(tmp_path)
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "track")]
    receipt = store.create_batch("client-1", requests)

    result = orchestrator.process_batch(receipt, requests)

    assert result.jobs[0].status is JobStatus.COMPLETED
    assert not storage.temporary_directory("id-track").exists()


def test_retry_waits_use_the_configured_delays(tmp_path):
    events = []
    downloader = FakeDownloader(tmp_path / "downloads", events, transient_failures={"id-track": 2})
    waits = []
    orchestrator, store, _, _, _, _ = build_orchestrator(
        tmp_path, downloader=downloader, delay_log=waits.append, retry_delays=(3.0, 6.0)
    )
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "track")]
    receipt = store.create_batch("client-1", requests)

    result = orchestrator.process_batch(receipt, requests)

    assert waits == [3.0, 6.0]
    assert result.jobs[0].status is JobStatus.COMPLETED


def test_exhausted_retries_fail_only_that_job_and_the_batch_continues(tmp_path):
    events = []
    downloader = FakeDownloader(tmp_path / "downloads", events, transient_failures={"id-flaky": 99})
    orchestrator, store, _, _, _, separator = build_orchestrator(tmp_path, downloader=downloader)
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "flaky"), SourceRequest(SourceKind.SEARCH_QUERY, "steady")]
    receipt = store.create_batch("client-1", requests)

    result = orchestrator.process_batch(receipt, requests)

    assert downloader.attempts["id-flaky"] == 3  # retry_count attempts, then give up
    assert [job.status for job in result.jobs] == [JobStatus.FAILED, JobStatus.COMPLETED]
    assert result.jobs[0].error_summary == "Temporary service error"  # safe text, no provider details
    assert separator.calls == ["id-steady"]
    assert result.status is BatchStatus.PARTIAL


def test_incomplete_stems_are_repaired_by_a_later_run(tmp_path):
    events = []
    broken = FakeSeparator(events, incomplete={"id-track"})
    orchestrator, store, storage, _, _, _ = build_orchestrator(tmp_path, separator=broken)
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "track")]
    first = orchestrator.process_batch(store.create_batch("client-1", requests), requests)
    assert first.jobs[0].status is JobStatus.FAILED

    repaired, store, storage, _, _, separator = build_orchestrator(tmp_path)
    second = repaired.process_batch(store.create_batch("client-1", requests), requests)

    assert second.jobs[0].status is JobStatus.COMPLETED
    assert separator.calls == ["id-track"]
    assert storage.stems_complete("id-track", STEMS)
    assert not storage.temporary_directory("id-track").exists()


def test_legacy_wav_stems_are_reused_with_their_format(tmp_path):
    orchestrator, store, storage, _, _, separator = build_orchestrator(tmp_path)
    for name in STEMS:
        storage.stem_file_target("id-track", name, extension="wav").path.write_bytes(b"RIFF")
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "track")]
    receipt = store.create_batch("client-1", requests)

    orchestrator.process_batch(receipt, requests)

    stems = [asset for asset in store.list_assets(receipt.job_ids[0], "client-1") if asset.stem_name]
    assert separator.calls == [] and {asset.media_format for asset in stems} == {"wav"}


def test_new_stems_are_stored_as_flac(tmp_path):
    orchestrator, store, storage, _, _, _ = build_orchestrator(tmp_path)
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "track")]
    receipt = store.create_batch("client-1", requests)

    orchestrator.process_batch(receipt, requests)

    stems = [asset for asset in store.list_assets(receipt.job_ids[0], "client-1") if asset.stem_name]
    assert len(stems) == 6 and {asset.media_format for asset in stems} == {"flac"}
    assert all(storage.resolve_path(asset.storage_key).read_bytes()[:4] == b"fLaC" for asset in stems)


def test_a_full_disk_fails_the_job_once_with_the_space_message(tmp_path):
    class FullDiskSeparator(FakeSeparator):
        def separate(self, audio_path, output_dir, options, progress=None):
            self.calls.append(audio_path.stem)
            raise disk_full_error(700 * 2**20, 100 * 2**20)

    separator = FullDiskSeparator([])
    orchestrator, store, _, _, _, _ = build_orchestrator(tmp_path, separator=separator)
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "track")]
    receipt = store.create_batch("client-1", requests)

    result = orchestrator.process_batch(receipt, requests)

    assert result.jobs[0].status is JobStatus.FAILED
    assert separator.calls == ["id-track"]  # not retried: waiting does not free disk space
    assert result.jobs[0].error_summary.startswith("Недостаточно места на диске: нужно ~700 МБ, свободно 100 МБ")


def test_progress_is_written_rarely_and_reaches_one(tmp_path, monkeypatch):
    class ChattySeparator(FakeSeparator):
        def separate(self, audio_path, output_dir, options, progress=None):
            for step in range(1, 1001):
                progress(step / 1000)
            return super().separate(audio_path, output_dir, options)

    orchestrator, store, _, _, _, _ = build_orchestrator(tmp_path, separator=ChattySeparator([]))
    writes = []
    original = store.set_job_progress
    monkeypatch.setattr(store, "set_job_progress", lambda job_id, value: writes.append(value) or original(job_id, value))
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "track")]
    receipt = store.create_batch("client-1", requests)

    orchestrator.process_batch(receipt, requests)

    assert len(writes) <= 3  # 1000 callbacks in well under 0.5 s: only the first and forced writes
    assert writes == sorted(writes) and 0.10 in writes
    assert store.list_projects("client-1")[0].job_progress == 1.0


def test_another_model_separates_the_same_song_again_into_its_own_set(tmp_path):
    orchestrator, store, storage, _, _, separator = build_orchestrator(tmp_path)
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "track")]
    first = store.create_batch("client-1", requests)
    orchestrator.process_batch(first, requests)
    second = store.create_batch("client-1", requests, separation_options=SeparationOptions("htdemucs_ft", "cpu"))

    orchestrator.process_batch(second, requests)

    assert separator.calls == ["id-track", "id-track"]  # the six-stem set was not reused for the other model
    keys = sorted(asset.storage_key for asset in store.list_assets(second.job_ids[0], "client-1") if asset.stem_name)
    assert keys == [f"Stems/id-track (htdemucs_ft)/{stem}.flac" for stem in ("bass", "drums", "other", "vocals")]


def test_the_song_length_is_remembered_after_the_download(tmp_path, monkeypatch):
    monkeypatch.setattr("audio_stems.pipeline.audio_duration", lambda path: 241.5)
    orchestrator, store, _, _, _, _ = build_orchestrator(tmp_path)
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "track")]
    receipt = store.create_batch("client-1", requests)

    orchestrator.process_batch(receipt, requests)

    assert store.list_projects("client-1")[0].audio_seconds == 241.5


def _finished_project(orchestrator, store, query):
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, query)]
    receipt = store.create_batch("local", requests)
    orchestrator.process_batch(receipt, requests)
    project = store.list_projects("local")[-1]
    return project, receipt.job_ids[0]


def _stem_keys(store, job_id):
    return sorted(a.storage_key for a in store.list_assets(job_id, "local") if a.asset_type is MediaAssetType.STEM)


def test_upgrade_switches_a_project_to_the_best_model_and_deletes_the_old_stems(tmp_path):
    orchestrator, store, storage, events, _, separator = build_orchestrator(tmp_path)
    project, job_id = _finished_project(orchestrator, store, "song")
    old_keys = _stem_keys(store, job_id)
    assert all(key.startswith(f"Stems/{project.title}/") for key in old_keys)
    store.queue_analysis(project.project_id, "upgrade", {"owner_id": "local"})
    progress = []
    store.set_analysis_progress = lambda *args: progress.append(args[-1])

    assert DatabaseWorker(store, orchestrator).run_once() is True  # no batch queued: the upgrade runs

    assert separator.options[-1].model_name == "bs_roformer_sw"
    new_keys = _stem_keys(store, job_id)
    assert len(new_keys) == 6 and all(key.startswith(f"Stems/{project.title} (bs_roformer_sw)/") for key in new_keys)
    assert all(storage.resolve_path(key).is_file() for key in new_keys)
    assert not any(storage.resolve_path(key).exists() for key in old_keys)  # the old stems are gone
    assert store.get_job(job_id, "local").separation_options.model_name == "bs_roformer_sw"
    assert store.get_analysis(project.project_id, "upgrade")["status"] == "done"
    assert not storage.temporary_directory(project.title, project.source_id).exists()
    # Running it again changes nothing: the project already has the best model.
    assert orchestrator.upgrade_project(project.project_id, "local") == ()


def test_upgrade_keeps_old_stems_another_project_still_uses(tmp_path):
    orchestrator, store, storage, _, _, _ = build_orchestrator(tmp_path)
    first, first_job = _finished_project(orchestrator, store, "song")
    _, second_job = _finished_project(orchestrator, store, "song")  # reuses the same stem files
    shared = _stem_keys(store, first_job)
    assert _stem_keys(store, second_job) == shared

    replaced = orchestrator.upgrade_project(first.project_id, "local")

    assert replaced == ()  # nothing to delete: the second project still plays them
    assert all(storage.resolve_path(key).is_file() for key in shared)
    assert _stem_keys(store, second_job) == shared


def test_upgrade_without_the_source_audio_fails_with_a_clear_message(tmp_path):
    orchestrator, store, storage, _, _, _ = build_orchestrator(tmp_path)
    project, job_id = _finished_project(orchestrator, store, "song")
    old_keys = _stem_keys(store, job_id)
    source = next(a for a in store.list_assets(job_id, "local") if a.asset_type is MediaAssetType.SOURCE_AUDIO)
    storage.resolve_path(source.storage_key).unlink()
    store.queue_analysis(project.project_id, "upgrade", {"owner_id": "local"})

    assert DatabaseWorker(store, orchestrator).run_once() is True

    state = store.get_analysis(project.project_id, "upgrade")
    assert state["status"] == "failed" and state["error"] == "Исходное аудио проекта не найдено на диске"
    assert _stem_keys(store, job_id) == old_keys  # the project keeps its stems


def test_queued_batches_go_before_upgrades(tmp_path):
    orchestrator, store, _, events, _, _ = build_orchestrator(tmp_path)
    project, _ = _finished_project(orchestrator, store, "song")
    store.queue_analysis(project.project_id, "upgrade", {"owner_id": "local"})
    store.create_batch("local", [SourceRequest(SourceKind.SEARCH_QUERY, "next")])
    worker = DatabaseWorker(store, orchestrator)

    worker.run_once()
    assert store.get_analysis(project.project_id, "upgrade")["status"] == "queued"
    worker.run_once()
    assert store.get_analysis(project.project_id, "upgrade")["status"] == "done"



def _uploaded(storage, data=b"fLaC" + b"\x01" * 60, name="My Song.flac"):
    partial = storage.new_upload_path()
    partial.write_bytes(data)
    return SourceRequest(SourceKind.LOCAL_FILE, f"{storage.finish_upload(partial, name)}/{name}")


def test_an_uploaded_file_is_separated_without_search_or_download_in_the_batch_order(tmp_path):
    orchestrator, store, storage, events, downloader, _ = build_orchestrator(tmp_path)
    local = _uploaded(storage)
    requests = [local, SourceRequest(SourceKind.SEARCH_QUERY, "second")]
    receipt = store.create_batch("client-1", requests)

    result = orchestrator.process_batch(receipt, requests)

    assert [job.status for job in result.jobs] == [JobStatus.COMPLETED, JobStatus.COMPLETED]
    assert [event for event in events if event[0] in {"search", "download"}] == [("search", "second"), ("download", "id-second")]
    assert [event[0] for event in events].index("separate") > [event[0] for event in events].index("download")
    assert (tmp_path / "media" / "My Song.flac").read_bytes() == b"fLaC" + b"\x01" * 60
    assert not storage.upload_path(local.value.split("/")[0]).exists()  # moved into the media folder
    project = next(p for p in store.list_projects("client-1") if p.input_type is SourceKind.LOCAL_FILE)
    assert project.title == "My Song" and project.source_id == local.value[:16]
    assert ("separate", "My Song") in events  # the stored copy, not the removed upload


def test_the_same_file_opened_again_reuses_its_stored_audio_and_a_lost_upload_fails_only_its_job(tmp_path):
    orchestrator, store, storage, events, _, _ = build_orchestrator(tmp_path)
    first = _uploaded(storage)
    orchestrator.process_batch(store.create_batch("client-1", [first]), [first])

    again = _uploaded(storage)  # same content, same token
    lost = _uploaded(storage, b"ID3" + b"\x02" * 40, "Other.mp3")
    storage.remove_upload(again.value.split("/")[0])
    storage.remove_upload(lost.value.split("/")[0])
    requests = [again, lost]
    result = orchestrator.process_batch(store.create_batch("client-1", requests), requests)

    assert again.value == first.value
    assert [job.status for job in result.jobs] == [JobStatus.COMPLETED, JobStatus.FAILED]
    assert result.jobs[1].error_summary == "Source was not found"
    assert sorted(path.name for path in (tmp_path / "media").glob("*.flac")) == ["My Song.flac"]

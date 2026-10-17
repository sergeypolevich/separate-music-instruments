from dataclasses import replace
from pathlib import Path

from contracts import (
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
)
from jobs import BatchOrchestrator
from local_storage import LocalMediaStorage
from metadata import SqliteMetadataStore
from settings import Settings


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

    def download(self, source):
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

    def separate(self, audio_path: Path, output_dir: Path, options: SeparationOptions):
        track = audio_path.stem
        self.events.append(("separate", track))
        self.calls.append(track)
        self.options.append(options)
        if track in self.failures:
            raise AdapterError("model failed", FailureCategory.PERMANENT)
        output_dir.mkdir(parents=True, exist_ok=True)
        names = STEMS[:-1] if track in self.incomplete else STEMS
        artifacts = []
        for name in names:
            path = output_dir / f"{name}.wav"
            path.write_bytes(b"stem-data")
            artifacts.append(StemArtifact(name, path))
        return SeparationResult(tuple(artifacts))


def build_orchestrator(tmp_path, *, downloader=None, separator=None, delay_log=None):
    events = []
    store = SqliteMetadataStore(tmp_path / "metadata.db")
    storage = LocalMediaStorage(tmp_path / "media")
    downloader = downloader or FakeDownloader(tmp_path / "downloads", events)
    separator = separator or FakeSeparator(events)
    settings = replace(
        Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"),
        retry_count=3,
        retry_delays=(0.0, 0.0),
    )
    orchestrator = BatchOrchestrator(
        repository=store,
        downloader=downloader,
        separator=separator,
        storage=storage,
        options=SeparationOptions("htdemucs_6s", "cpu"),
        expected_stems=STEMS,
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
    selected = SeparationOptions("htdemucs", "cpu", {"shifts": 2})
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "Track")]
    receipt = orchestrator.submit_batch("owner-1", requests, separation_options=selected)

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

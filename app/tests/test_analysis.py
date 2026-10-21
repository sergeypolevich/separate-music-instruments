import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
import numpy as np
import pytest
import soundfile

from audio_stems import analysis
from audio_stems.api import create_app
from audio_stems.config import Settings
from audio_stems.db import SqliteMetadataStore
from audio_stems.models import BatchStatus, JobStatus, MediaAsset, MediaAssetType, SourceKind, SourceRequest
from audio_stems.storage import LocalMediaStorage


def make_client(tmp_path, detect):
    settings = replace(Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"), storage_root=tmp_path / "media")
    repository = SqliteMetadataStore(tmp_path / "beats.db")
    storage = LocalMediaStorage(settings.storage_root)
    runs = []
    # The real app starts a separate process; here the queue runs inline with a fake detector.
    start = lambda: runs.append(analysis.run_queue(repository, detect, tick=0.01))
    app = create_app(repository=repository, settings=settings, storage=storage, start_analysis=start)

    def request(method, path):
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                return await client.request(method, path)
        return asyncio.run(send())

    return request, repository, storage, runs


def add_song(repository, storage, *, completed=True, with_source=True):
    receipt = repository.create_batch("local", [SourceRequest(SourceKind.SEARCH_QUERY, "Song")])
    job_id = receipt.job_ids[0]
    if completed:
        for status in (JobStatus.DOWNLOADING, JobStatus.DOWNLOADED, JobStatus.SEPARATING, JobStatus.COMPLETED):
            repository.transition_job(job_id, status)
        repository.transition_batch(receipt.batch_id, BatchStatus.PROCESSING)
        repository.transition_batch(receipt.batch_id, BatchStatus.COMPLETED)
    repository.set_start_seconds(job_id, 1.25)
    if with_source:
        source = storage.audio_target("Song", "webm", "song-1")
        source.path.write_bytes(b"\x1aE\xdf\xa3audio")
        repository.add_asset(MediaAsset(uuid4(), job_id, MediaAssetType.SOURCE_AUDIO, source.storage_key, 9, media_format="webm"))
    stem = storage.stem_file_target("Song", "vocals", "song-1")
    soundfile.write(stem.path, np.zeros((8000 * 120, 1), np.float32), 8000, format="FLAC", subtype="PCM_16")
    repository.add_asset(MediaAsset(uuid4(), job_id, MediaAssetType.STEM, stem.storage_key, 4, "vocals", "flac"))
    return repository.list_projects("local")[-1].project_id


def test_beats_are_detected_once_on_request_and_stored(tmp_path):
    calls = []

    def fake_detect(path, start):
        calls.append((path.name, start))
        return {"beats": [0.0, 0.5, 1.0], "downbeats": [0.0], "bpm": 120.0}

    request, repository, storage, runs = make_client(tmp_path, fake_detect)
    project_id = add_song(repository, storage)
    assert request("GET", f"/api/v1/projects/{project_id}/beats").json()["status"] == "none"

    first = request("POST", f"/api/v1/projects/{project_id}/beats")
    again = request("POST", f"/api/v1/projects/{project_id}/beats")

    assert first.status_code == 202 and again.status_code == 200
    assert again.json() == {"status": "done", "progress": 1.0, "error": None,
                            "result": {"beats": [0.0, 0.5, 1.0], "downbeats": [0.0], "bpm": 120.0}}
    assert calls == [("Song.webm", 1.25)]  # the source audio, measured from the music start, once
    assert runs == [1]


def test_a_failed_analysis_reports_why_and_can_be_requested_again(tmp_path):
    outcomes = iter([RuntimeError("model crashed"), {"beats": [0.0], "downbeats": [], "bpm": None}])

    def flaky(path, start):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    request, repository, storage, _ = make_client(tmp_path, flaky)
    project_id = add_song(repository, storage)

    request("POST", f"/api/v1/projects/{project_id}/beats")
    failed = request("GET", f"/api/v1/projects/{project_id}/beats").json()
    request("POST", f"/api/v1/projects/{project_id}/beats")

    assert failed["status"] == "failed" and failed["error"] == "Не удалось определить доли этого трека"
    assert request("GET", f"/api/v1/projects/{project_id}/beats").json()["status"] == "done"


def test_beats_need_a_finished_project_with_its_source_audio(tmp_path):
    request, repository, storage, runs = make_client(tmp_path, lambda path, start: {})
    running = add_song(repository, storage, completed=False)
    no_source = add_song(repository, storage, with_source=False)

    assert request("POST", f"/api/v1/projects/{running}/beats").status_code == 409
    assert request("POST", f"/api/v1/projects/{no_source}/beats").json()["error"]["code"] == "source_unavailable"
    assert request("POST", f"/api/v1/projects/{uuid4()}/beats").status_code == 404
    assert runs == []


def test_progress_is_estimated_while_running_and_never_passes_95_percent(tmp_path, monkeypatch):
    repository = SqliteMetadataStore(tmp_path / "beats.db")
    storage = LocalMediaStorage(tmp_path / "media")
    project_id = add_song(repository, storage)
    repository.queue_analysis(project_id, "beats", {"audio_path": "x.webm", "start_seconds": 0, "minutes": 0.001})
    seen = []
    monkeypatch.setattr(analysis, "SECONDS_PER_MINUTE", 0.0)  # expected time collapses to the 5 s floor
    monkeypatch.setattr(analysis, "STARTUP_SECONDS", 0.0)

    def slow_detect(path, start):
        import time
        deadline = time.monotonic() + 5  # wait for the reporter thread, however loaded the machine is
        while repository.get_analysis(project_id, "beats")["progress"] == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        seen.append(repository.get_analysis(project_id, "beats")["progress"])
        return {"beats": [], "downbeats": [], "bpm": None}

    analysis.run_queue(repository, slow_detect, tick=0.02)

    assert 0 < seen[0] <= 0.95 and repository.get_analysis(project_id, "beats")["progress"] == 1.0


def test_an_analysis_interrupted_by_closing_the_app_starts_again_with_the_api(tmp_path):
    repository = SqliteMetadataStore(tmp_path / "beats.db")
    storage = LocalMediaStorage(tmp_path / "media")
    project_id = add_song(repository, storage)
    repository.queue_analysis(project_id, "beats", {"audio_path": "x.webm"})
    repository.claim_analysis("beats")  # the runner took it, then the app was closed

    _, _, _, runs = make_client(tmp_path, lambda path, start: {"beats": [], "downbeats": [], "bpm": None})

    assert runs == [1] and repository.get_analysis(project_id, "beats")["status"] == "done"


def test_detected_beats_are_shifted_to_song_time(monkeypatch, tmp_path):
    class FakeTracker:
        def __init__(self, **_kwargs):
            pass

        def __call__(self, samples, rate):
            return np.array([0.9, 1.5, 2.0, 2.5, 3.0]), np.array([1.5, 3.0])

    import beat_this.inference
    monkeypatch.setattr(beat_this.inference, "Audio2Beats", FakeTracker)
    monkeypatch.setattr(analysis, "decode_mono", lambda path: np.zeros(10, np.float32))
    monkeypatch.setattr(analysis, "beat_checkpoint", lambda: tmp_path / "final0.ckpt")

    result = analysis.detect_beats(tmp_path / "song.webm", 1.5)

    assert result == {"beats": [0.0, 0.5, 1.0, 1.5], "downbeats": [0.0, 1.5], "bpm": 120.0}


_CHECKPOINT = Path.home() / ".cache" / "torch" / "hub" / "checkpoints" / "beat_this-final0.ckpt"


def test_beat_checkpoint_is_downloaded_once_and_must_match_its_hash(monkeypatch, tmp_path):
    import hashlib
    import torch
    monkeypatch.setattr(torch.hub, "get_dir", lambda: str(tmp_path))
    monkeypatch.setattr(analysis, "BEAT_CHECKPOINT_SHA256", hashlib.sha256(b"weights").hexdigest())
    downloads = []

    def download(url, target, content=b"weights"):
        downloads.append(url)
        Path(target).write_bytes(content)

    path = analysis.beat_checkpoint(download)
    assert path == tmp_path / "checkpoints" / "beat_this-final0.ckpt" and downloads == [analysis.BEAT_CHECKPOINT_URL]
    assert analysis.beat_checkpoint(download) == path and len(downloads) == 1  # cached: no second download
    path.write_bytes(b"swapped")
    with pytest.raises(RuntimeError):
        analysis.beat_checkpoint(download)
    assert not path.exists()  # fetched again on the next use


@pytest.mark.skipif(not _CHECKPOINT.is_file(), reason="beat_this weights are not downloaded yet")
def test_the_downloaded_beat_checkpoint_matches_the_pinned_hash():
    assert analysis.beat_checkpoint() == _CHECKPOINT


@pytest.mark.skipif(not _CHECKPOINT.is_file(), reason="beat_this weights are not downloaded yet (no network in unit tests)")
def test_real_beat_tracking_finds_a_steady_120_bpm_click(tmp_path):
    rate = 44100
    signal = np.zeros(rate * 12, np.float32)
    click = (np.sin(2 * np.pi * 1000 * np.arange(800) / rate) * np.exp(-np.arange(800) / 120)).astype(np.float32)
    for beat in range(24):
        signal[int(beat * 0.5 * rate):int(beat * 0.5 * rate) + 800] += click
    path = tmp_path / "click.wav"
    soundfile.write(path, signal, rate)

    result = analysis.detect_beats(path, 0.0)

    assert result["bpm"] == pytest.approx(120, abs=2)

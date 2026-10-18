import asyncio
from pathlib import Path
from dataclasses import replace
from uuid import uuid4

import httpx

from audio_stems.api import create_app
from audio_stems.models import (
    AdapterError,
    BatchStatus,
    FailureCategory,
    JobStatus,
    MediaAsset,
    MediaAssetType,
    SourceKind,
    SourceRequest,
)
from audio_stems.separator import StemSeparator
from audio_stems.storage import LocalMediaStorage
from audio_stems.db import SqliteMetadataStore
from audio_stems.config import Settings
from audio_stems.models import YouTubeSourceAdapter


def make_delete_client(tmp_path):
    settings = replace(
        Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"),
        database_url=f"sqlite:///{(tmp_path / 'delete.db').as_posix()}",
        storage_root=tmp_path / "media",
    )
    repository = SqliteMetadataStore(tmp_path / "delete.db")
    storage = LocalMediaStorage(settings.storage_root)
    app = create_app(
        repository=repository,
        settings=settings,
        source_adapter=YouTubeSourceAdapter(settings.allowed_source_hosts),
        separator=StemSeparator(),
        storage=storage,
    )

    async def send(method, path):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.request(method, path)

    def request(method, path):
        return asyncio.run(send(method, path))

    request.app = app
    return request, repository, storage


def add_project(repository, storage, title, *, completed=True):
    receipt = repository.create_batch("local", [SourceRequest(SourceKind.SEARCH_QUERY, title)])
    job_id = receipt.job_ids[0]
    project = repository.list_projects("local")[-1]
    if completed:
        repository.transition_job(job_id, JobStatus.DOWNLOADING, stage="download")
        repository.transition_job(job_id, JobStatus.DOWNLOADED, stage="download")
        repository.transition_job(job_id, JobStatus.SEPARATING, stage="separation")
        repository.transition_job(job_id, JobStatus.COMPLETED, stage="completed")
        repository.transition_batch(receipt.batch_id, BatchStatus.PROCESSING)
        repository.transition_batch(receipt.batch_id, BatchStatus.COMPLETED)
    target = storage.audio_target(title, "webm", title)
    target.path.write_bytes(b"media")
    repository.add_asset(MediaAsset(uuid4(), job_id, MediaAssetType.SOURCE_AUDIO, target.storage_key, 5, media_format="webm"))
    stem = storage.stem_file_target(title, "vocals", title)
    stem.path.write_bytes(b"stem")
    repository.add_asset(MediaAsset(uuid4(), job_id, MediaAssetType.STEM, stem.storage_key, 4, "vocals", "wav"))
    return project, receipt, [target.path, stem.path]


def test_delete_removes_owned_metadata_and_files_but_preserves_neighbor(tmp_path):
    request, repository, storage = make_delete_client(tmp_path)
    project, _, files = add_project(repository, storage, "Delete me")
    neighbor, _, neighbor_files = add_project(repository, storage, "Keep me")

    response = request("DELETE", f"/api/v1/projects/{project.project_id}")
    repeat = request("DELETE", f"/api/v1/projects/{project.project_id}")

    assert response.status_code == repeat.status_code == 204
    assert repository.get_project(project.project_id, "local") is None
    assert all(not path.exists() for path in files)
    assert repository.get_project(neighbor.project_id, "local") is not None
    assert all(path.exists() for path in neighbor_files)


def test_failed_file_cleanup_is_persisted_and_delete_retries_it(tmp_path, monkeypatch):
    request, repository, storage = make_delete_client(tmp_path)
    project, _, files = add_project(repository, storage, "Retry cleanup")
    original_delete = storage.delete_asset
    calls = 0

    def fail_after_one(storage_key):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AdapterError("disk unavailable", FailureCategory.TRANSIENT)
        original_delete(storage_key)

    monkeypatch.setattr(storage, "delete_asset", fail_after_one)

    failed = request("DELETE", f"/api/v1/projects/{project.project_id}")
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "cleanup_pending"
    assert repository.get_project(project.project_id, "local") is None
    assert not files[0].exists() and files[1].exists()

    monkeypatch.setattr(storage, "delete_asset", original_delete)
    retried = request("DELETE", f"/api/v1/projects/{project.project_id}")
    assert retried.status_code == 204
    assert all(not path.exists() for path in files)


def test_processing_project_is_not_removed_while_worker_may_be_writing(tmp_path):
    request, repository, storage = make_delete_client(tmp_path)
    project, receipt, files = add_project(repository, storage, "Active", completed=False)
    repository.transition_batch(receipt.batch_id, BatchStatus.PROCESSING)
    repository.transition_job(receipt.job_ids[0], JobStatus.DOWNLOADING, stage="download")

    response = request("DELETE", f"/api/v1/projects/{project.project_id}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_processing"
    assert repository.get_project(project.project_id, "local") is not None
    assert all(path.exists() for path in files)


def test_delete_queued_single_job_cancels_its_batch(tmp_path):
    request, repository, storage = make_delete_client(tmp_path)
    project, receipt, _files = add_project(repository, storage, "Queued", completed=False)

    response = request("DELETE", f"/api/v1/projects/{project.project_id}")
    batch = repository.get_batch(receipt.batch_id, "local")

    assert response.status_code == 204
    assert batch.status == BatchStatus.CANCELLED.value


def test_delete_queued_item_from_multi_job_batch_is_rejected_without_cancelling_siblings(tmp_path):
    request, repository, _storage = make_delete_client(tmp_path)
    receipt = repository.create_batch(
        "local", [SourceRequest(SourceKind.SEARCH_QUERY, "First"), SourceRequest(SourceKind.SEARCH_QUERY, "Second")]
    )
    project = repository.list_projects("local")[0]

    response = request("DELETE", f"/api/v1/projects/{project.project_id}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_processing"
    assert repository.get_project(project.project_id, "local") is not None
    assert len(repository.list_jobs_for_batch(receipt.batch_id, "local")) == 2


def test_delete_rejects_foreign_project_and_unsafe_storage_key(tmp_path):
    request, repository, storage = make_delete_client(tmp_path)
    project, _, _ = add_project(repository, storage, "Private")
    repository.create_batch("someone-else", [SourceRequest(SourceKind.SEARCH_QUERY, "Foreign")])
    foreign_project = repository.list_projects("someone-else")[0]
    hidden = request("DELETE", f"/api/v1/projects/{foreign_project.project_id}")
    outside = tmp_path / "outside.txt"
    outside.write_text("preserve", encoding="utf-8")
    repository.add_asset(MediaAsset(
        uuid4(), project.job_id, MediaAssetType.STEM, "../outside.txt", 7, "unsafe", "txt"
    ))

    rejected = request("DELETE", f"/api/v1/projects/{project.project_id}")

    assert hidden.status_code == 404
    assert rejected.status_code == 503
    assert rejected.json()["error"]["code"] == "cleanup_pending"
    assert outside.read_text(encoding="utf-8") == "preserve"


def test_files_shared_with_another_project_stay_and_the_size_says_so(tmp_path):
    request, repository, storage = make_delete_client(tmp_path)
    first, _, files = add_project(repository, storage, "Same song")
    second_receipt = repository.create_batch("local", [SourceRequest(SourceKind.SEARCH_QUERY, "Same song again")])
    second_job = second_receipt.job_ids[0]
    for asset in repository.list_assets(repository.list_projects("local")[0].job_id, "local"):  # reused source and stems
        repository.add_asset(MediaAsset(uuid4(), second_job, asset.asset_type, asset.storage_key, asset.byte_size,
                                        asset.stem_name, asset.media_format))

    detail = request("GET", f"/api/v1/projects/{first.project_id}").json()
    assert (detail["disk_bytes"], detail["shared_bytes"]) == (0, 9)

    assert request("DELETE", f"/api/v1/projects/{first.project_id}").status_code == 204
    assert all(path.exists() for path in files)  # the other project still plays them
    second = repository.list_projects("local")[0]
    assert request("GET", f"/api/v1/projects/{second.project_id}").json()["disk_bytes"] == 9


def test_delete_removes_the_empty_stem_folder_and_the_failed_separation_leftovers(tmp_path):
    request, repository, storage = make_delete_client(tmp_path)
    project, _, files = add_project(repository, storage, "Leftovers")
    repository.update_project_source(project.job_id, "Leftovers", "Leftovers")
    leftovers = storage.temporary_directory("Leftovers", "Leftovers")
    leftovers.mkdir(parents=True)
    (leftovers / "Leftovers.wav").write_bytes(b"float audio")
    assert request("GET", f"/api/v1/projects/{project.project_id}").json()["disk_bytes"] == 9

    assert request("DELETE", f"/api/v1/projects/{project.project_id}").status_code == 204

    assert not files[1].parent.exists() and not leftovers.exists()
    assert storage.stems_root.exists()  # only the project's own folder goes


def test_temp_folder_stays_while_another_job_of_the_same_song_is_running(tmp_path):
    request, repository, storage = make_delete_client(tmp_path)
    old, _, _ = add_project(repository, storage, "Busy song")
    repository.update_project_source(old.job_id, "busy-id", "Busy song")
    running, _, _ = add_project(repository, storage, "Busy song (other model)", completed=False)
    repository.update_project_source(running.job_id, "busy-id", "Busy song")
    work = storage.temporary_directory("Busy song", "busy-id")
    work.mkdir(parents=True)

    assert request("DELETE", f"/api/v1/projects/{old.project_id}").status_code == 204
    assert work.exists()


def test_a_file_locked_for_a_moment_is_retried_before_cleanup_is_left_pending(tmp_path, monkeypatch):
    _, repository, storage = make_delete_client(tmp_path)
    _, _, files = add_project(repository, storage, "Locked")
    real_unlink = Path.unlink
    failures = {"left": 2}

    def flaky_unlink(self, missing_ok=False):
        if self == files[0] and failures["left"]:
            failures["left"] -= 1
            raise PermissionError(32, "The process cannot access the file because it is being used")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    storage.delete_asset(storage._key_for(files[0]), pause=0)
    assert not files[0].exists() and failures["left"] == 0


def test_an_unfinished_cleanup_is_finished_when_the_api_starts(tmp_path, monkeypatch):
    request, repository, storage = make_delete_client(tmp_path)
    project, _, files = add_project(repository, storage, "Pending")
    monkeypatch.setattr(storage, "delete_asset", lambda *_args, **_kwargs: (_ for _ in ()).throw(AdapterError("locked", FailureCategory.TRANSIENT)))
    assert request("DELETE", f"/api/v1/projects/{project.project_id}").status_code == 503
    monkeypatch.undo()
    assert repository.pending_project_deletions() and files[0].exists()

    make_delete_client(tmp_path)  # a new API process on the same data

    assert not files[0].exists() and repository.pending_project_deletions() == []



def test_a_stem_removed_by_hand_is_reported_unavailable_and_the_project_still_deletes(tmp_path):
    request, repository, storage = make_delete_client(tmp_path)
    project, _, files = add_project(repository, storage, "Hand removed")
    files[1].unlink()  # the user deleted vocals in Explorer

    detail = request("GET", f"/api/v1/projects/{project.project_id}").json()
    vocals = next(asset for asset in detail["assets"] if asset["stem_name"] == "vocals")

    assert vocals["available"] is False and detail["disk_bytes"] == 5
    assert request("GET", f"/api/v1/assets/{vocals['asset_id']}").status_code == 404
    assert request("GET", f"/api/v1/assets/{vocals['asset_id']}/waveform").status_code == 404
    assert request("DELETE", f"/api/v1/projects/{project.project_id}").status_code == 204
    assert not files[0].exists()


def test_storage_cleanup_needs_confirmation_and_waits_for_running_jobs(tmp_path):
    request, repository, storage = make_delete_client(tmp_path)
    _, _, files = add_project(repository, storage, "In use")
    (storage.root / "orphan.webm").write_bytes(b"orphan!")
    running, _, _ = add_project(repository, storage, "Running", completed=False)

    report = request("GET", "/api/v1/storage").json()
    assert report["unused_bytes"] == 7 and report["projects_bytes"] == 18 and report["busy"] is True

    async def post(body):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=request.app), base_url="http://testserver") as client:
            return await client.post("/api/v1/storage/cleanup", json=body)

    assert asyncio.run(post({"confirmed": True})).status_code == 409  # a job is running
    repository.transition_job(running.job_id, JobStatus.CANCELLED, stage="test")
    assert asyncio.run(post({"confirmed": False})).status_code == 409
    response = asyncio.run(post({"confirmed": True}))

    assert response.status_code == 200 and response.json()["freed_bytes"] == 7
    assert not (storage.root / "orphan.webm").exists() and all(path.exists() for path in files)


def test_the_music_start_is_measured_once_and_waveforms_begin_there(tmp_path, monkeypatch):
    request, repository, storage = make_delete_client(tmp_path)
    project, _, files = add_project(repository, storage, "Quiet intro")
    import numpy as np
    import soundfile
    rate = 8000
    soundfile.write(files[1], np.vstack([np.zeros((rate, 1)), np.full((rate, 1), 0.5)]), rate, subtype="PCM_16")
    calls = []
    real = storage.music_start
    monkeypatch.setattr(storage, "music_start", lambda paths: calls.append(paths) or real(paths))

    first = request("GET", f"/api/v1/projects/{project.project_id}").json()
    second = request("GET", f"/api/v1/projects/{project.project_id}").json()

    assert abs(first["start_seconds"] - 0.98) < 1e-3 and second["start_seconds"] == first["start_seconds"]
    assert len(calls) == 1  # stored after the first open
    vocals = next(asset for asset in first["assets"] if asset["stem_name"] == "vocals")
    waveform = request("GET", f"/api/v1/assets/{vocals['asset_id']}/waveform").json()
    assert abs(waveform["duration_seconds"] - 1.02) < 1e-3 and waveform["levels"][-1] > 0.4
    assert min(waveform["levels"][:5]) == 0.0  # only the 20 ms pre-roll is silent

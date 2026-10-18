import asyncio
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

    return lambda method, path: asyncio.run(send(method, path)), repository, storage


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

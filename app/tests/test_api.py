import asyncio
from dataclasses import replace
from uuid import UUID, uuid4

import httpx

from audio_stems.models import JobStatus, SourceKind, SourceRequest
from audio_stems.separator import StemSeparator
from audio_stems.db import SqliteMetadataStore
from audio_stems.config import Settings
from audio_stems.models import YouTubeSourceAdapter
from audio_stems.api import create_app


class SyncASGIClient:
    """Use the ASGI transport directly to avoid deprecated Starlette/httpx glue."""

    def __init__(self, app):
        self.app = app

    def request(self, method, url, *, json=None, content=None, headers=None):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
            ) as client:
                return await client.request(method, url, json=json, content=content, headers=headers)

        return asyncio.run(send())

    def get(self, url):
        return self.request("GET", url)

    def post(self, url, *, json):
        return self.request("POST", url, json=json)


def build_client(tmp_path):
    settings = replace(
        Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"),
        database_url=f"sqlite:///{(tmp_path / 'audio_stems.api.db').as_posix()}",
        batch_limit=2,
    )
    repository = SqliteMetadataStore(tmp_path / "audio_stems.api.db")
    app = create_app(
        repository=repository,
        settings=settings,
        source_adapter=YouTubeSourceAdapter(settings.allowed_source_hosts),
        separator=StemSeparator(),
    )
    return SyncASGIClient(app), repository


def test_api_creates_queued_batch_and_returns_without_processing(tmp_path):
    client, repository = build_client(tmp_path)

    response = client.post(
        "/api/v1/batches",
        json={"inputs": ["Artist - Song title", "https://youtu.be/video-2"]},
    )

    assert response.status_code == 202
    payload = response.json()
    assert len(payload["job_ids"]) == 2
    batch = repository.get_batch(UUID(payload["batch_id"]), "local")
    assert batch.status == "queued"
    assert repository.get_job(UUID(payload["job_ids"][0]), "local").status is JobStatus.QUEUED


def test_api_rejects_unsupported_urls_and_never_accepts_client_paths(tmp_path):
    client, _ = build_client(tmp_path)

    bad_source = client.post("/api/v1/batches", json={"inputs": ["https://example.com/video"]})
    client_path = client.post("/api/v1/batches", json={"inputs": ["query"], "output_path": "../../outside"})

    assert bad_source.status_code == 422
    assert bad_source.json()["error"]["code"] == "invalid_source"
    assert client_path.status_code == 422
    assert client_path.json()["error"]["code"] == "invalid_request"


def test_api_enforces_batch_limit_and_validates_model_settings(tmp_path):
    client, _ = build_client(tmp_path)

    too_many = client.post("/api/v1/batches", json={"inputs": ["one", "two", "three"]})
    bad_model = client.post(
        "/api/v1/batches",
        json={"inputs": ["query"], "model_name": "made-up-model"},
    )
    # Demucs parameters are no longer part of the API; extra fields are rejected.
    bad_parameter = client.post(
        "/api/v1/batches",
        json={"inputs": ["query"], "parameters": {"shifts": 1}},
    )

    assert too_many.status_code == 422
    assert too_many.json()["error"]["code"] == "batch_limit_exceeded"
    assert bad_model.status_code == 422
    assert bad_model.json()["error"]["code"] == "invalid_model_options"
    assert bad_parameter.status_code == 422
    assert bad_parameter.json()["error"]["code"] == "invalid_request"


def test_api_reads_batches_projects_and_models_with_stable_not_found(tmp_path):
    client, repository = build_client(tmp_path)
    request = [SourceRequest(SourceKind.SEARCH_QUERY, "Track")]
    receipt = repository.create_batch("local", request)
    job_id = receipt.job_ids[0]
    repository.transition_job(job_id, JobStatus.DOWNLOADING, stage="download")

    batch = client.get(f"/api/v1/batches/{receipt.batch_id}")
    projects = client.get("/api/v1/projects")
    models = client.get("/api/v1/models")
    hidden = client.get(f"/api/v1/batches/{uuid4()}")

    assert batch.status_code == 200 and batch.json()["jobs"][0]["status"] == "downloading"
    assert batch.json()["jobs"][0]["current_stage"] == "download"
    assert projects.json()[0]["input_value"] == "Track"
    assert [model["name"] for model in models.json() if model["default"]] == ["hdemucs_mmi"]
    assert hidden.status_code == 404 and hidden.json()["error"]["code"] == "not_found"
    assert client.get("/api/v1/batches").status_code == 404  # list endpoint removed; only POST remains
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 404


def test_batch_reads_are_owner_scoped_to_server_side_local_identity(tmp_path):
    client, repository = build_client(tmp_path)
    foreign = repository.create_batch("another-owner", [SourceRequest(SourceKind.SEARCH_QUERY, "private")])

    response = client.get(f"/api/v1/batches/{foreign.batch_id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"



def test_batch_records_the_best_detected_device_and_has_no_device_selection(tmp_path, monkeypatch):
    monkeypatch.setattr("audio_stems.separator.detect_compute_devices", lambda: ("cuda", "cpu"))
    client, repository = build_client(tmp_path)

    created = client.post("/api/v1/batches", json={"inputs": ["Track"]})
    with_device = client.post("/api/v1/batches", json={"inputs": ["Track"], "device": "cpu"})

    assert created.status_code == 202
    job = repository.get_job(UUID(created.json()["job_ids"][0]), "local")
    assert job.separation_options.device == "cuda"
    assert client.get("/api/v1/devices").status_code == 404
    assert with_device.status_code == 422 and with_device.json()["error"]["code"] == "invalid_request"


def build_upload_client(tmp_path):
    from audio_stems.storage import LocalMediaStorage

    settings = replace(
        Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"),
        storage_root=tmp_path / "Music", temp_root=tmp_path / "Music" / "_demucs_temp", upload_limit_mb=1,
    )
    repository = SqliteMetadataStore(tmp_path / "audio_stems.api.db")
    storage = LocalMediaStorage(settings.storage_root, temp_root=settings.temp_root)
    app = create_app(repository=repository, settings=settings, separator=StemSeparator(), storage=storage)
    return SyncASGIClient(app), repository, settings.temp_root / "uploads"


def test_an_uploaded_audio_file_becomes_a_queued_project_named_after_the_file(tmp_path):
    client, repository, uploads = build_upload_client(tmp_path)
    flac = b"fLaC" + bytes(range(256)) * 4

    response = client.request("POST", "/api/v1/uploads", content=flac,
                              headers={"X-Filename": "C%3A%5Cmusic%5CMy%20Song%20%C3%A9.flac"})

    assert response.status_code == 202, response.text
    job = repository.get_job(UUID(response.json()["job_ids"][0]), "local")
    assert job.status is JobStatus.QUEUED
    token, name = job.input_value.split("/", 1)
    assert name == "My Song é.flac"  # the folder part of the name is dropped
    assert (uploads / token).read_bytes() == flac and [path.name for path in uploads.iterdir()] == [token]
    project, = client.get("/api/v1/projects").json()
    assert project["input_type"] == "local_file" and project["title"] == "My Song é"
    assert project["source_id"] == token[:16]


def test_an_upload_that_is_not_audio_too_large_or_unnamed_is_refused_and_leaves_nothing(tmp_path):
    client, repository, uploads = build_upload_client(tmp_path)

    def upload(content, name="song.mp3", **extra):
        headers = {"X-Filename": name} if name is not None else {}
        return client.request("POST", "/api/v1/uploads" + extra.get("query", ""), content=content, headers=headers)

    assert upload(b"MZ\x90\x00 not audio").json()["error"]["code"] == "unsupported_file"
    assert upload(b"ID3 audio", name="song.exe").status_code == 415
    assert upload(b"ID3 audio", name=None).status_code == 400
    assert upload(b"ID3" + b"\x00" * (2**20), name="big.mp3").status_code == 413  # by Content-Length

    async def chunks():  # no Content-Length: the limit is counted while receiving
        for _ in range(3):
            yield b"ID3" + b"\x00" * (2**19)
    response = upload(chunks(), name="streamed.mp3")
    assert response.status_code == 413 and response.json()["error"]["code"] == "file_too_large"
    assert upload(b"ID3 audio", query="?model_name=no-such-model").status_code == 422

    assert repository.list_projects("local") == []
    assert not uploads.exists() or list(uploads.iterdir()) == []

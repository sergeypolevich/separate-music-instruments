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

    def request(self, method, url, *, json=None):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
            ) as client:
                return await client.request(method, url, json=json)

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
        json={"inputs": ["Portishead - Glory Box", "https://youtu.be/video-2"]},
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
    assert "htdemucs_6s" in [model["name"] for model in models.json()]
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

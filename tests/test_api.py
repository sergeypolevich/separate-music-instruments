import asyncio
from dataclasses import replace
from uuid import UUID, uuid4

import httpx

from contracts import JobStatus, SourceKind, SourceRequest
from demucs_adapter import DemucsSeparator
from metadata import SqliteMetadataStore
from settings import Settings
from sources import YouTubeSourceAdapter
from api import create_app


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
        database_url=f"sqlite:///{(tmp_path / 'api.db').as_posix()}",
        batch_limit=2,
    )
    repository = SqliteMetadataStore(tmp_path / "api.db")
    app = create_app(
        repository=repository,
        settings=settings,
        source_adapter=YouTubeSourceAdapter(settings.allowed_source_hosts),
        separator=DemucsSeparator(),
    )
    return SyncASGIClient(app), repository


def test_api_creates_queued_batch_and_returns_without_processing(tmp_path):
    client, repository = build_client(tmp_path)

    response = client.post(
        "/api/v1/batches",
        json={"inputs": ["Portishead - Glory Box", "https://youtu.be/video-2"], "idempotency_key": "request-1"},
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
    bad_parameter = client.post(
        "/api/v1/batches",
        json={"inputs": ["query"], "parameters": {"unknown": 1}},
    )

    assert too_many.status_code == 422
    assert too_many.json()["error"]["code"] == "batch_limit_exceeded"
    assert bad_model.status_code == 422
    assert bad_model.json()["error"]["code"] == "invalid_model_options"
    assert bad_parameter.status_code == 422
    assert bad_parameter.json()["error"]["code"] == "invalid_model_options"


def test_api_reads_jobs_events_projects_and_models_with_stable_not_found(tmp_path):
    client, repository = build_client(tmp_path)
    request = [SourceRequest(SourceKind.SEARCH_QUERY, "Track")]
    receipt = repository.create_batch("local", request)
    job_id = receipt.job_ids[0]
    repository.transition_job(job_id, JobStatus.DOWNLOADING, stage="download")

    batch = client.get(f"/api/v1/batches/{receipt.batch_id}")
    job = client.get(f"/api/v1/jobs/{job_id}")
    events = client.get(f"/api/v1/jobs/{job_id}/events")
    projects = client.get("/api/v1/projects")
    batches = client.get("/api/v1/batches")
    models = client.get("/api/v1/models")
    hidden = client.get(f"/api/v1/jobs/{uuid4()}")

    assert batch.status_code == 200 and batch.json()["jobs"][0]["status"] == "downloading"
    assert job.json()["current_stage"] == "download"
    assert [event["name"] for event in events.json()] == ["job_created", "job_state_changed"]
    assert projects.json()[0]["input_value"] == "Track"
    assert batches.json()[0]["batch_id"] == str(receipt.batch_id)
    assert "htdemucs_6s" in [model["name"] for model in models.json()]
    assert hidden.status_code == 404 and hidden.json()["error"]["code"] == "not_found"


def test_job_reads_are_owner_scoped_to_server_side_local_identity(tmp_path):
    client, repository = build_client(tmp_path)
    foreign = repository.create_batch("another-owner", [SourceRequest(SourceKind.SEARCH_QUERY, "private")])

    response = client.get(f"/api/v1/jobs/{foreign.job_ids[0]}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_idempotency_key_returns_the_existing_batch_without_duplicate_jobs(tmp_path):
    client, repository = build_client(tmp_path)
    body = {"inputs": ["Track"], "idempotency_key": "same-request"}

    first = client.post("/api/v1/batches", json=body)
    repeated = client.post("/api/v1/batches", json=body)

    assert repeated.status_code == 202
    assert repeated.json()["batch_id"] == first.json()["batch_id"]
    assert repeated.json()["job_ids"] == first.json()["job_ids"]
    assert len(repository.list_projects("local")) == 1

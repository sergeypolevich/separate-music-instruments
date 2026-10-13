import asyncio
from dataclasses import replace
from pathlib import Path

import httpx

from api import create_app
from contracts import JobStatus, SourceKind, SourceRequest
from demucs_adapter import DemucsSeparator
from metadata import SqliteMetadataStore
from settings import Settings
from sources import YouTubeSourceAdapter


def get_app_client(tmp_path):
    settings = replace(
        Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"),
        database_url=f"sqlite:///{(tmp_path / 'web.db').as_posix()}",
    )
    repository = SqliteMetadataStore(tmp_path / "web.db")
    app = create_app(
        repository=repository,
        settings=settings,
        source_adapter=YouTubeSourceAdapter(settings.allowed_source_hosts),
        separator=DemucsSeparator(),
    )

    async def request(method, path):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.request(method, path)

    return (lambda method, path: asyncio.run(request(method, path))), repository


def test_api_serves_submission_page_and_static_bundle(tmp_path):
    request, _repository = get_app_client(tmp_path)

    page = request("GET", "/")
    script = request("GET", "/app.js")
    styles = request("GET", "/app.css")
    playback = request("GET", "/playback.mjs")

    assert page.status_code == script.status_code == styles.status_code == playback.status_code == 200
    assert "Audio Stems" in page.text
    assert 'id="inputs"' in page.text
    assert 'id="model"' in page.text
    assert 'id="check-updates"' in page.text
    assert 'id="release-list"' in page.text
    assert 'play.dataset.action = "play"' in script.text
    assert 'gain.dataset.control = "gain"' in script.text
    assert 'mute.dataset.control = "mute"' in script.text
    assert 'href="app.css"' in page.text
    assert 'src="app.js"' in page.text
    assert "/static/" not in page.text
    assert "class SynchronizedTransport" in playback.text
    assert "window.confirm" in script.text
    assert "/api/v1/updates/apply" in script.text
    assert "release.updatable" in script.text
    assert "requirements.txt" in script.text
    assert "http://" not in styles.text and "https://" not in styles.text


def test_ui_covers_persisted_job_states_and_renders_every_sibling_safely():
    script = Path(__file__).parents[1] / "web" / "app.js"
    source = script.read_text(encoding="utf-8")

    for state in ("queued", "downloading", "separating", "completed", "failed"):
        assert state in source
    assert "batch.jobs.map" in source
    assert "textContent" in source
    assert "/api/v1/batches/" in source
    assert "/waveform?levels=128" in source


def test_ui_progress_data_keeps_a_failed_job_visible_beside_a_successful_one(tmp_path):
    request, repository = get_app_client(tmp_path)
    receipt = repository.create_batch(
        "local",
        [
            SourceRequest(SourceKind.SEARCH_QUERY, "Broken track"),
            SourceRequest(SourceKind.SEARCH_QUERY, "Working track"),
        ],
    )
    failed_id, completed_id = receipt.job_ids
    repository.transition_job(failed_id, JobStatus.FAILED, stage="download", error_summary="Download failed")
    repository.transition_job(completed_id, JobStatus.DOWNLOADING, stage="download")
    repository.transition_job(completed_id, JobStatus.DOWNLOADED, stage="download")
    repository.transition_job(completed_id, JobStatus.SEPARATING, stage="separation")
    repository.transition_job(completed_id, JobStatus.COMPLETED, stage="completed")

    response = request("GET", f"/api/v1/batches/{receipt.batch_id}")
    jobs = response.json()["jobs"]

    assert [job["input_value"] for job in jobs] == ["Broken track", "Working track"]
    assert [job["status"] for job in jobs] == ["failed", "completed"]
    assert jobs[0]["error_summary"] == "Download failed"

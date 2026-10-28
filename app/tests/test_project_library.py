import asyncio
from dataclasses import replace
from uuid import uuid4

import httpx

from audio_stems.api import create_app
from audio_stems.models import (
    JobStatus,
    MediaAsset,
    MediaAssetType,
    SeparationOptions,
    SourceKind,
    SourceRequest,
)
from audio_stems.separator import StemSeparator
from audio_stems.storage import LocalMediaStorage
from audio_stems.db import SqliteMetadataStore
from audio_stems.config import Settings
from audio_stems.models import YouTubeSourceAdapter


def build_reopened_client(tmp_path):
    database = tmp_path / "library.db"
    media_root = tmp_path / "media"
    settings = replace(
        Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"),
        database_url=f"sqlite:///{database.as_posix()}",
        storage_root=media_root,
    )
    first_store = SqliteMetadataStore(database)
    storage = LocalMediaStorage(media_root)
    options = SeparationOptions("htdemucs_6s", "cpu")
    receipt = first_store.create_batch(
        "local", [SourceRequest(SourceKind.SEARCH_QUERY, "Search query")],
        separation_options=options,
    )
    job_id = receipt.job_ids[0]
    first_store.update_project_source(job_id, "video-1", "Actual Song Title")
    first_store.transition_job(job_id, JobStatus.DOWNLOADING, stage="download")
    first_store.transition_job(job_id, JobStatus.DOWNLOADED, stage="download")
    first_store.transition_job(job_id, JobStatus.SEPARATING, stage="separation")

    audio_path = storage.root / "Actual Song Title.webm"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"audio")
    first_store.add_asset(
        MediaAsset(uuid4(), job_id, MediaAssetType.SOURCE_AUDIO, "Actual Song Title.webm", 5, media_format="webm")
    )
    stem_path = storage.stem_file_target("Actual Song Title", "vocals", "video-1", "wav").path
    stem_path.write_bytes(b"stem")
    first_store.add_asset(
        MediaAsset(
            uuid4(), job_id, MediaAssetType.STEM, "Stems/Actual Song Title/vocals.wav",
            4, stem_name="vocals", media_format="wav",
        )
    )
    missing_path = storage.stem_file_target("Actual Song Title", "drums", "video-1", "wav").path
    first_store.add_asset(
        MediaAsset(
            uuid4(), job_id, MediaAssetType.STEM, "Stems/Actual Song Title/drums.wav",
            0, stem_name="drums", media_format="wav",
        )
    )

    reopened = SqliteMetadataStore(database)
    app = create_app(
        repository=reopened,
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

    return lambda method, path: asyncio.run(send(method, path)), receipt, options, missing_path


def test_library_reopens_project_with_title_status_options_and_asset_availability(tmp_path):
    request, receipt, options, missing_path = build_reopened_client(tmp_path)
    missing_path.unlink(missing_ok=True)

    projects = request("GET", "/api/v1/projects")
    project = projects.json()[0]
    detail = request("GET", f"/api/v1/projects/{project['project_id']}")
    body = detail.json()

    assert project["title"] == "Actual Song Title"
    assert project["source_id"] == "video-1"
    assert project["job_status"] == "separating"
    assert body["job"]["status"] == "separating"
    assert body["job"]["separation_options"] == {
        "model_name": options.model_name, "device": options.device
    }
    assets = {asset["stem_name"] or "source_audio": asset for asset in body["assets"]}
    assert assets["source_audio"]["available"] is True
    assert assets["vocals"]["available"] is True
    assert assets["drums"]["available"] is False
    assert all("storage_key" not in asset and "path" not in asset for asset in body["assets"])
    assert body["project_id"] == str(project["project_id"])
    assert body["job"]["job_id"] == str(receipt.job_ids[0])


def test_project_library_rejects_foreign_and_unknown_ids(tmp_path):
    request, _, _, _ = build_reopened_client(tmp_path)
    store = SqliteMetadataStore(tmp_path / "library.db")
    foreign = store.create_batch("foreign-owner", [SourceRequest(SourceKind.SEARCH_QUERY, "Private")])
    foreign_project = store.list_projects("foreign-owner")[0]

    hidden = request("GET", f"/api/v1/projects/{foreign_project.project_id}")
    missing = request("GET", f"/api/v1/projects/{uuid4()}")

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"]["code"] == "not_found"
    assert missing.json()["error"]["code"] == "not_found"


def test_ui_has_project_list_and_reopen_detail_controls():
    from pathlib import Path

    page = Path(__file__).parents[1] / "web" / "index.html"
    script = page.with_name("app.js")
    markup = page.read_text(encoding="utf-8")
    source = script.read_text(encoding="utf-8")

    assert 'id="project-tabs"' in markup
    assert 'id="project-workspace"' in markup
    assert 'id="playback-panel"' in markup
    assert 'id="delete-project"' in markup
    assert 'method: "DELETE"' in source
    assert 'id="confirm-dialog"' in markup
    assert "confirmDialog.showModal()" in source
    assert "window.confirm(" not in source and "window.alert(" not in source  # the app's own dialog everywhere
    assert "/api/v1/projects/" in source
    assert 'await import("./playback.mjs")' in source
    assert '!["wav", "flac"].includes(asset.media_format)' in source
    assert "const path = `/api/v1/assets/${encodeURIComponent(asset.asset_id)}`" in source
    assert 'audio.crossOrigin = "anonymous"' in source
    assert 'new MixerSession(gainNodes,' in source
    assert 'mixer.setGain(index, percent / 100)' in source
    assert 'mixer.setMute(index, muted)' in source
    assert 'mixer.setSolo(index, selected)' in source
    assert "available" in source

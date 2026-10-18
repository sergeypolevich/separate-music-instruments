import sqlite3
from uuid import uuid4

import pytest

from audio_stems.models import (
    JobStatus,
    MediaAsset,
    MediaAssetType,
    SourceKind,
    SourceRequest,
    SeparationOptions,
)
from audio_stems.db import SqliteMetadataStore


def test_metadata_relations_events_and_projects_survive_reopening(tmp_path):
    database = tmp_path / "audio_stems.db.db"
    store = SqliteMetadataStore(database)
    receipt = store.create_batch(
        "client-1",
        [
            SourceRequest(SourceKind.SEARCH_QUERY, "Track one"),
            SourceRequest(SourceKind.VIDEO_URL, "https://youtube.com/watch?v=video-2"),
        ],
        idempotency_key="request-1",
    )
    job_id = receipt.job_ids[0]
    batch = store.get_batch(receipt.batch_id, "client-1")
    with sqlite3.connect(database) as connection:
        client_name = connection.execute("SELECT display_name FROM clients WHERE id = 'client-1'").fetchone()
    updated = store.transition_job(job_id, JobStatus.DOWNLOADING, stage="download")
    assert updated.status is JobStatus.DOWNLOADING
    assert batch is not None and batch.status == "queued"
    assert client_name == ("client-1",)

    reopened = SqliteMetadataStore(database)
    job = reopened.get_job(job_id, "client-1")
    projects = reopened.list_projects("client-1")
    events = reopened.list_events(job_id, "client-1")

    assert job is not None and job.status is JobStatus.DOWNLOADING
    assert job.current_stage == "download"
    assert len(projects) == 2
    assert {project.input_value for project in projects} == {
        "Track one",
        "https://youtube.com/watch?v=video-2",
    }
    assert [event.name for event in events] == ["job_created", "job_state_changed"]
    assert events[-1].details["status"] == "downloading"
    assert reopened.get_job(job_id, "another-client") is None
    assert reopened.get_batch(receipt.batch_id, "another-client") is None


def test_assets_are_metadata_with_relative_keys_not_media_blobs(tmp_path):
    database = tmp_path / "audio_stems.db.db"
    store = SqliteMetadataStore(database)
    receipt = store.create_batch("client-1", [SourceRequest(SourceKind.SEARCH_QUERY, "Track")])
    job_id = receipt.job_ids[0]
    asset = MediaAsset(
        uuid4(),
        job_id,
        MediaAssetType.STEM,
        "Stems/Track/vocals.wav",
        1234,
        stem_name="vocals",
        media_format="wav",
        checksum="abc123",
    )

    store.add_asset(asset)
    reopened = SqliteMetadataStore(database)
    assets = reopened.list_assets(job_id, "client-1")
    connection = sqlite3.connect(database)
    try:
        job_columns = {row[1].lower() for row in connection.execute("PRAGMA table_info(jobs)")}
        asset_columns = {row[1].lower() for row in connection.execute("PRAGMA table_info(media_assets)")}
    finally:
        connection.close()

    assert assets == [asset]
    assert not assets[0].storage_key.startswith(("/", "\\"))
    assert "blob" not in job_columns | asset_columns
    assert "media_data" not in job_columns | asset_columns


def test_schema_migrations_are_repeatable_and_enable_foreign_keys(tmp_path):
    database = tmp_path / "audio_stems.db.db"
    store = SqliteMetadataStore(database)
    SqliteMetadataStore(database)
    connection = sqlite3.connect(database)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()

    with store._connection() as connection:
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO projects(id, owner_id, input_type, input_value, created_at, updated_at) "
                "VALUES ('orphan', 'missing-client', 'search_query', 'track', 'now', 'now')"
            )

    assert version == 4
    assert foreign_keys == 1
    assert {"clients", "projects", "batches", "jobs", "media_assets", "job_events", "pending_project_deletions"} <= tables


def test_invalid_job_state_transition_is_rejected_and_not_persisted(tmp_path):
    store = SqliteMetadataStore(tmp_path / "audio_stems.db.db")
    receipt = store.create_batch("client-1", [SourceRequest(SourceKind.SEARCH_QUERY, "Track")])
    job_id = receipt.job_ids[0]

    with pytest.raises(ValueError):
        store.transition_job(job_id, JobStatus.COMPLETED)

    assert store.get_job(job_id, "client-1").status is JobStatus.QUEUED
    assert [event.name for event in store.list_events(job_id, "client-1")] == ["job_created"]


def test_job_separation_options_survive_database_reopen(tmp_path):
    database = tmp_path / "audio_stems.db.db"
    store = SqliteMetadataStore(database)
    options = SeparationOptions("htdemucs_6s", "cpu", {"shifts": 2, "overlap": 0.5})

    receipt = store.create_batch(
        "client-1", [SourceRequest(SourceKind.SEARCH_QUERY, "Track")],
        separation_options=options,
    )
    reopened = SqliteMetadataStore(database)
    job = reopened.get_job(receipt.job_ids[0], "client-1")

    assert job.separation_options == options
    assert receipt.separation_options == options


def test_idempotent_resubmission_returns_original_separation_options(tmp_path):
    store = SqliteMetadataStore(tmp_path / "audio_stems.db.db")
    request = [SourceRequest(SourceKind.SEARCH_QUERY, "Track")]
    original = SeparationOptions("htdemucs_6s", "cpu", {"shifts": 2})
    changed = SeparationOptions("htdemucs", "cpu", {})
    first = store.create_batch("client-1", request, "same-key", original)

    repeated = store.create_batch("client-1", request, "same-key", changed)

    assert repeated.batch_id == first.batch_id
    assert repeated.separation_options == original

import sqlite3
from uuid import uuid4

import pytest

from contracts import (
    JobStatus,
    JobRepository,
    MediaAsset,
    MediaAssetType,
    SourceKind,
    SourceRequest,
)
from metadata import SqliteMetadataStore


def test_metadata_relations_events_and_projects_survive_reopening(tmp_path):
    database = tmp_path / "metadata.db"
    store = SqliteMetadataStore(database)
    assert isinstance(store, JobRepository)
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
    client = store.get_client("client-1")
    updated = store.transition_job(job_id, JobStatus.DOWNLOADING, stage="download")
    assert updated.status is JobStatus.DOWNLOADING
    assert batch is not None and batch.status == "queued"
    assert client is not None and client.display_name == "client-1"

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
    database = tmp_path / "metadata.db"
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
    database = tmp_path / "metadata.db"
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

    assert version == 1
    assert foreign_keys == 1
    assert {"clients", "projects", "batches", "jobs", "media_assets", "job_events"} <= tables


def test_invalid_job_state_transition_is_rejected_and_not_persisted(tmp_path):
    store = SqliteMetadataStore(tmp_path / "metadata.db")
    receipt = store.create_batch("client-1", [SourceRequest(SourceKind.SEARCH_QUERY, "Track")])
    job_id = receipt.job_ids[0]

    with pytest.raises(ValueError):
        store.transition_job(job_id, JobStatus.COMPLETED)

    assert store.get_job(job_id, "client-1").status is JobStatus.QUEUED
    assert [event.name for event in store.list_events(job_id, "client-1")] == ["job_created"]

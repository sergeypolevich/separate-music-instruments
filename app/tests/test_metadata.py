import sqlite3
import subprocess
import sys
from pathlib import Path
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

    assert version == 8
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
    options = SeparationOptions("htdemucs_6s", "cpu")

    receipt = store.create_batch(
        "client-1", [SourceRequest(SourceKind.SEARCH_QUERY, "Track")],
        separation_options=options,
    )
    reopened = SqliteMetadataStore(database)
    job = reopened.get_job(receipt.job_ids[0], "client-1")

    assert job.separation_options == options
    assert receipt.separation_options == options


def test_options_stored_before_version_9_with_parameters_still_load(tmp_path):
    database = tmp_path / "audio_stems.db.db"
    store = SqliteMetadataStore(database)
    receipt = store.create_batch("client-1", [SourceRequest(SourceKind.SEARCH_QUERY, "Old track")],
                                 separation_options=SeparationOptions("htdemucs_6s", "cpu"))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE jobs SET separation_options_json = ?",
            ('{"model_name": "htdemucs_6s", "device": "cpu", "parameters": {"shifts": 2}}',),
        )

    job = store.get_job(receipt.job_ids[0], "client-1")
    claimed = store.claim_next_batch()

    assert job.separation_options == SeparationOptions("htdemucs_6s", "cpu")
    assert claimed.receipt.separation_options == SeparationOptions("htdemucs_6s", "cpu")


def test_job_progress_only_rises_and_completion_fills_it(tmp_path):
    store = SqliteMetadataStore(tmp_path / "audio_stems.db")
    receipt = store.create_batch("client-1", [SourceRequest(SourceKind.SEARCH_QUERY, "Track")])
    job_id = receipt.job_ids[0]
    progress = lambda: store.list_projects("client-1")[0].job_progress

    assert progress() == 0
    store.set_job_progress(job_id, 0.4)
    store.set_job_progress(job_id, 0.2)
    assert progress() == 0.4
    for status in (JobStatus.SEARCHING, JobStatus.DOWNLOADING, JobStatus.DOWNLOADED, JobStatus.SEPARATING, JobStatus.COMPLETED):
        store.transition_job(job_id, status)
    assert progress() == 1.0
    assert store.get_project(store.list_projects("client-1")[0].project_id, "client-1").job_progress == 1.0


def test_two_processes_migrating_a_new_database_at_once_both_succeed(tmp_path):
    database = tmp_path / "audio_stems.db"
    started = [subprocess.Popen([sys.executable, "-c", "import sys; from audio_stems.db import SqliteMetadataStore; SqliteMetadataStore(sys.argv[1])", str(database)],
                                cwd=Path(__file__).parents[1], stderr=subprocess.PIPE) for _ in range(4)]
    results = [(process.wait(60), process.stderr.read().decode()) for process in started]

    assert all(code == 0 for code, _ in results), results
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 8


def test_a_genuinely_broken_migration_still_fails(tmp_path, monkeypatch):
    broken = tmp_path / "migrations"
    broken.mkdir()
    (broken / "001_broken.sql").write_text("BEGIN IMMEDIATE; ALTER TABLE missing ADD COLUMN x TEXT; COMMIT;", encoding="utf-8")
    monkeypatch.setattr("audio_stems.db.Path.with_name", lambda self, name: broken)
    with pytest.raises(sqlite3.OperationalError):
        SqliteMetadataStore(tmp_path / "audio_stems.db")


def test_separation_time_is_recorded_and_the_usual_speed_is_the_median_of_the_last_five(tmp_path):
    import time
    store = SqliteMetadataStore(tmp_path / "audio_stems.db")

    def separated(model, audio_seconds, separation_seconds):
        receipt = store.create_batch("client-1", [SourceRequest(SourceKind.SEARCH_QUERY, "Song")],
                                     separation_options=SeparationOptions(model, "cpu"))
        job_id = receipt.job_ids[0]
        for status in (JobStatus.DOWNLOADING, JobStatus.DOWNLOADED, JobStatus.SEPARATING, JobStatus.COMPLETED):
            store.transition_job(job_id, status)
        store.set_audio_seconds(job_id, audio_seconds)
        with sqlite3.connect(tmp_path / "audio_stems.db") as connection:
            connection.execute("UPDATE jobs SET separation_seconds = ? WHERE id = ?", (separation_seconds, str(job_id)))
        return job_id

    receipt = store.create_batch("client-1", [SourceRequest(SourceKind.SEARCH_QUERY, "Timed")])
    job_id = receipt.job_ids[0]
    for status in (JobStatus.DOWNLOADING, JobStatus.DOWNLOADED, JobStatus.SEPARATING):
        store.transition_job(job_id, status)
    time.sleep(0.05)
    store.transition_job(job_id, JobStatus.COMPLETED)
    with sqlite3.connect(tmp_path / "audio_stems.db") as connection:
        started, seconds = connection.execute("SELECT separation_started_at, separation_seconds FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
    assert started is not None and 0.03 < seconds < 5

    for rate in (1.0, 9.0, 2.0, 3.0, 4.0):
        separated("bs_roformer_sw", 100, rate * 100)
    separated("htdemucs_6s", 200, 180)
    rates = SqliteMetadataStore(tmp_path / "audio_stems.db").separation_rates()
    assert rates["bs_roformer_sw"] == 3.0 and rates["htdemucs_6s"] == 0.9

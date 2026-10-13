"""SQLite metadata repository; media bytes always remain in managed storage."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import UUID, uuid4

from contracts import (
    AdapterError,
    BatchReceipt,
    FailureCategory,
    JobEvent,
    JobStatus,
    JobView,
    MediaAsset,
    MediaAssetType,
    SourceKind,
    SourceRequest,
)


@dataclass(frozen=True)
class ProjectRecord:
    project_id: UUID
    owner_id: str
    input_type: SourceKind
    input_value: str
    source_id: str | None
    title: str | None
    created_at: datetime


@dataclass(frozen=True)
class ClientRecord:
    client_id: str
    display_name: str
    created_at: datetime


@dataclass(frozen=True)
class BatchRecord:
    batch_id: UUID
    owner_id: str
    submission_method: str
    status: str
    result_summary: str | None
    created_at: datetime


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SqliteMetadataStore:
    """Persist clients, projects, batches, jobs, assets, and job events."""

    def __init__(self, database_path: Path | str) -> None:
        if str(database_path) == ":memory:":
            raise ValueError("Use a temporary SQLite file so metadata survives repository reconnections")
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.database_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _migrate(self) -> None:
        migration_dir = Path(__file__).with_name("migrations")
        migrations = sorted(migration_dir.glob("[0-9][0-9][0-9]_*.sql"))
        with self._connection() as connection:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            for migration in migrations:
                version = int(migration.name.split("_", 1)[0])
                if version <= current:
                    continue
                script = migration.read_text(encoding="utf-8")
                connection.executescript(script)
                current = version
            latest = max((int(path.name.split("_", 1)[0]) for path in migrations), default=0)
            if current > latest:
                raise RuntimeError("Database schema is newer than this application")

    @staticmethod
    def _job_view(row: sqlite3.Row) -> JobView:
        return JobView(
            job_id=UUID(row["id"]),
            batch_id=UUID(row["batch_id"]),
            owner_id=row["owner_id"],
            status=JobStatus(row["status"]),
            current_stage=row["current_stage"],
            error_summary=row["error_summary"],
        )

    @staticmethod
    def _asset(row: sqlite3.Row) -> MediaAsset:
        return MediaAsset(
            asset_id=UUID(row["id"]),
            job_id=UUID(row["job_id"]),
            asset_type=MediaAssetType(row["asset_type"]),
            storage_key=row["storage_key"],
            byte_size=row["byte_size"],
            stem_name=row["stem_name"],
            media_format=row["media_format"],
            checksum=row["checksum"],
        )

    def create_batch(
        self,
        owner_id: str,
        sources: list[SourceRequest] | tuple[SourceRequest, ...],
        idempotency_key: str | None = None,
    ) -> BatchReceipt:
        if not owner_id or not sources:
            raise AdapterError("A batch needs an owner and at least one source", FailureCategory.INVALID_INPUT)

        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO clients(id, display_name, created_at) VALUES (?, ?, ?)",
                (owner_id, owner_id, now),
            )
            if idempotency_key:
                existing = connection.execute(
                    "SELECT id FROM batches WHERE owner_id = ? AND idempotency_key = ?",
                    (owner_id, idempotency_key),
                ).fetchone()
                if existing:
                    jobs = connection.execute("SELECT id FROM jobs WHERE batch_id = ? ORDER BY rowid", (existing["id"],)).fetchall()
                    return BatchReceipt(UUID(existing["id"]), tuple(UUID(row["id"]) for row in jobs))

            batch_id = uuid4()
            methods = {source.kind for source in sources}
            submission_method = "mixed" if len(methods) > 1 else next(iter(methods)).value
            connection.execute(
                """INSERT INTO batches
                   (id, owner_id, submission_method, status, idempotency_key, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (str(batch_id), owner_id, submission_method, "queued", idempotency_key, now, now),
            )

            job_ids: list[UUID] = []
            for source in sources:
                project_id = uuid4()
                job_id = uuid4()
                connection.execute(
                    """INSERT INTO projects
                       (id, owner_id, input_type, input_value, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (str(project_id), owner_id, source.kind.value, source.value, now, now),
                )
                connection.execute(
                    """INSERT INTO jobs
                       (id, batch_id, project_id, owner_id, input_type, normalized_source, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(job_id), str(batch_id), str(project_id), owner_id,
                        source.kind.value, source.value, JobStatus.QUEUED.value, now, now,
                    ),
                )
                connection.execute(
                    """INSERT INTO job_events
                       (id, batch_id, job_id, name, occurred_at, details_json, actor_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid4()), str(batch_id), str(job_id), "job_created", now,
                        json.dumps({"status": JobStatus.QUEUED.value}), owner_id,
                    ),
                )
                job_ids.append(job_id)
        return BatchReceipt(batch_id, tuple(job_ids))

    def transition_job(
        self,
        job_id: UUID,
        status: JobStatus,
        *,
        stage: str | None = None,
        error_summary: str | None = None,
    ) -> JobView:
        now = _utc_now()
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
            if row is None:
                raise AdapterError("Job was not found", FailureCategory.NOT_FOUND)
            connection.execute(
                "UPDATE jobs SET status = ?, current_stage = ?, error_summary = ?, updated_at = ? WHERE id = ?",
                (status.value, stage, error_summary, now, str(job_id)),
            )
            details: dict[str, object] = {"status": status.value}
            if stage is not None:
                details["stage"] = stage
            if error_summary is not None:
                details["error_summary"] = error_summary
            connection.execute(
                """INSERT INTO job_events
                   (id, batch_id, job_id, name, occurred_at, details_json, actor_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()), row["batch_id"], str(job_id), "job_state_changed", now,
                    json.dumps(details, ensure_ascii=False), row["owner_id"],
                ),
            )
            updated = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
            assert updated is not None
            return self._job_view(updated)

    def get_job(self, job_id: UUID, owner_id: str) -> JobView | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ? AND owner_id = ?",
                (str(job_id), owner_id),
            ).fetchone()
        return self._job_view(row) if row else None

    def get_client(self, client_id: str) -> ClientRecord | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        if row is None:
            return None
        return ClientRecord(row["id"], row["display_name"], _parse_datetime(row["created_at"]))

    def get_batch(self, batch_id: UUID, owner_id: str) -> BatchRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM batches WHERE id = ? AND owner_id = ?",
                (str(batch_id), owner_id),
            ).fetchone()
        if row is None:
            return None
        return BatchRecord(
            batch_id=UUID(row["id"]),
            owner_id=row["owner_id"],
            submission_method=row["submission_method"],
            status=row["status"],
            result_summary=row["result_summary"],
            created_at=_parse_datetime(row["created_at"]),
        )

    def list_events(self, job_id: UUID, owner_id: str) -> list[JobEvent]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT e.* FROM job_events e JOIN jobs j ON j.id = e.job_id
                   WHERE e.job_id = ? AND j.owner_id = ? ORDER BY e.occurred_at, e.rowid""",
                (str(job_id), owner_id),
            ).fetchall()
        return [
            JobEvent(
                event_id=UUID(row["id"]),
                job_id=UUID(row["job_id"]),
                name=row["name"],
                occurred_at=_parse_datetime(row["occurred_at"]),
                details=json.loads(row["details_json"]),
            )
            for row in rows
        ]

    def add_asset(self, asset: MediaAsset) -> None:
        now = _utc_now()
        with self._connection() as connection:
            job = connection.execute("SELECT 1 FROM jobs WHERE id = ?", (str(asset.job_id),)).fetchone()
            if job is None:
                raise AdapterError("Asset job was not found", FailureCategory.NOT_FOUND)
            connection.execute(
                """INSERT INTO media_assets
                   (id, job_id, asset_type, stem_name, storage_key, media_format, byte_size, checksum, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(asset.asset_id), str(asset.job_id), asset.asset_type.value,
                    asset.stem_name, asset.storage_key, asset.media_format,
                    asset.byte_size, asset.checksum, now,
                ),
            )

    def list_assets(self, job_id: UUID, owner_id: str) -> list[MediaAsset]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT a.* FROM media_assets a JOIN jobs j ON j.id = a.job_id
                   WHERE a.job_id = ? AND j.owner_id = ? ORDER BY a.created_at, a.rowid""",
                (str(job_id), owner_id),
            ).fetchall()
        return [self._asset(row) for row in rows]

    def list_projects(self, owner_id: str) -> list[ProjectRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM projects WHERE owner_id = ? ORDER BY created_at, rowid",
                (owner_id,),
            ).fetchall()
        return [
            ProjectRecord(
                project_id=UUID(row["id"]),
                owner_id=row["owner_id"],
                input_type=SourceKind(row["input_type"]),
                input_value=row["input_value"],
                source_id=row["source_id"],
                title=row["title"],
                created_at=_parse_datetime(row["created_at"]),
            )
            for row in rows
        ]

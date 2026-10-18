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

from audio_stems.models import (
    AdapterError,
    BatchStatus,
    BatchReceipt,
    BatchView,
    FailureCategory,
    JobEvent,
    JobStatus,
    JobView,
    QueuedBatch,
    MediaAsset,
    MediaAssetType,
    SourceKind,
    SourceRequest,
    SeparationOptions,
)


_ALLOWED_JOB_TRANSITIONS = {
    JobStatus.QUEUED: {JobStatus.SEARCHING, JobStatus.DOWNLOADING, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.SEARCHING: {JobStatus.DOWNLOADING, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.DOWNLOADING: {JobStatus.DOWNLOADED, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.DOWNLOADED: {JobStatus.SEPARATING, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.SEPARATING: {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.FAILED: {JobStatus.QUEUED},
    JobStatus.COMPLETED: set(),
    JobStatus.CANCELLED: set(),
}
_ALLOWED_BATCH_TRANSITIONS = {
    BatchStatus.QUEUED: {BatchStatus.PROCESSING, BatchStatus.CANCELLED},
    BatchStatus.PROCESSING: {
        BatchStatus.COMPLETED,
        BatchStatus.PARTIAL,
        BatchStatus.FAILED,
        BatchStatus.CANCELLED,
    },
    BatchStatus.COMPLETED: set(),
    BatchStatus.PARTIAL: set(),
    BatchStatus.FAILED: set(),
    BatchStatus.CANCELLED: set(),
}


@dataclass(frozen=True)
class ProjectRecord:
    project_id: UUID
    owner_id: str
    input_type: SourceKind
    input_value: str
    source_id: str | None
    title: str | None
    created_at: datetime
    job_id: UUID
    job_status: str
    job_progress: float = 0.0


@dataclass(frozen=True)
class ProjectDeletion:
    project_id: UUID
    owner_id: str
    storage_keys: tuple[str, ...]
    # Known only on the first call; a resumed cleanup has just the file keys.
    title: str | None = None
    source_id: str | None = None


BatchRecord = BatchView


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _options_from_json(raw: str | None) -> SeparationOptions | None:
    # Rows written before version 9 also carry a "parameters" key; only model and device are used.
    if not raw:
        return None
    data = json.loads(raw)
    return SeparationOptions(data["model_name"], data["device"])


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
                try:
                    connection.executescript(script)
                except sqlite3.IntegrityError:
                    # The API and worker start together: another process claimed this version first.
                    connection.rollback()
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
            retry_count=row["retry_count"],
            separation_options=_options_from_json(row["separation_options_json"]),
            input_value=row["normalized_source"],
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
        separation_options: SeparationOptions | None = None,
    ) -> BatchReceipt:
        if not owner_id or not sources:
            raise AdapterError("A batch needs an owner and at least one source", FailureCategory.INVALID_INPUT)

        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO clients(id, display_name, created_at) VALUES (?, ?, ?)",
                (owner_id, owner_id, now),
            )
            batch_id = uuid4()
            methods = {source.kind for source in sources}
            submission_method = "mixed" if len(methods) > 1 else next(iter(methods)).value
            connection.execute(
                """INSERT INTO batches
                   (id, owner_id, submission_method, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(batch_id), owner_id, submission_method, "queued", now, now),
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
                       (id, batch_id, project_id, owner_id, input_type, normalized_source, status,
                        separation_options_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(job_id), str(batch_id), str(project_id), owner_id,
                        source.kind.value, source.value, JobStatus.QUEUED.value,
                        json.dumps({
                            "model_name": separation_options.model_name,
                            "device": separation_options.device,
                        }) if separation_options else None,
                        now, now,
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
        return BatchReceipt(batch_id, tuple(job_ids), owner_id, separation_options)

    def claim_next_batch(self) -> QueuedBatch | None:
        """Atomically claim one queued batch for a single worker process."""
        with self._connection() as connection:
            batch = connection.execute(
                "SELECT id, owner_id FROM batches WHERE status = ? ORDER BY created_at, rowid LIMIT 1",
                (BatchStatus.QUEUED.value,),
            ).fetchone()
            if batch is None:
                return None
            now = _utc_now()
            updated = connection.execute(
                "UPDATE batches SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (BatchStatus.PROCESSING.value, now, batch["id"], BatchStatus.QUEUED.value),
            )
            if updated.rowcount != 1:
                return None
            rows = connection.execute(
                "SELECT id, input_type, normalized_source, separation_options_json "
                "FROM jobs WHERE batch_id = ? ORDER BY rowid",
                (batch["id"],),
            ).fetchall()
            if not rows:
                raise AdapterError("Queued batch has no jobs", FailureCategory.PERMANENT)
            sources = tuple(SourceRequest(SourceKind(row["input_type"]), row["normalized_source"]) for row in rows)
            options = _options_from_json(rows[0]["separation_options_json"])
            job_ids = tuple(UUID(row["id"]) for row in rows)
            for job_id in job_ids:
                connection.execute(
                    "INSERT INTO job_events(id, batch_id, job_id, name, occurred_at, details_json, actor_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                    (str(uuid4()), batch["id"], str(job_id), "batch_claimed", now,
                     json.dumps({"status": BatchStatus.PROCESSING.value})),
                )
        receipt = BatchReceipt(UUID(batch["id"]), job_ids, batch["owner_id"], options)
        return QueuedBatch(receipt, sources)

    def recover_interrupted_batches(self) -> int:
        """Fail unfinished work from prior worker processes; outputs remain reusable."""
        recovered = 0
        with self._connection() as connection:
            batches = connection.execute(
                "SELECT id FROM batches WHERE status = ? ORDER BY created_at, rowid",
                (BatchStatus.PROCESSING.value,),
            ).fetchall()
            for batch in batches:
                rows = connection.execute(
                    "SELECT id, status FROM jobs WHERE batch_id = ? ORDER BY rowid", (batch["id"],)
                ).fetchall()
                if not rows:
                    final_status = BatchStatus.FAILED
                    completed = failed = 0
                else:
                    now = _utc_now()
                    for row in rows:
                        status = JobStatus(row["status"])
                        if status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
                            continue
                        summary = "Interrupted by worker failure; submit the source again to retry"
                        connection.execute(
                            "UPDATE jobs SET status = ?, current_stage = ?, error_summary = ?, updated_at = ? WHERE id = ?",
                            (JobStatus.FAILED.value, "worker_recovery", summary, now, row["id"]),
                        )
                        connection.execute(
                            "INSERT INTO job_events(id, batch_id, job_id, name, occurred_at, details_json, actor_id) "
                            "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                            (str(uuid4()), batch["id"], row["id"], "job_state_changed", now,
                             json.dumps({"status": JobStatus.FAILED.value, "stage": "worker_recovery",
                                         "error_summary": summary})),
                        )
                    counts = connection.execute(
                        "SELECT status, COUNT(*) AS count FROM jobs WHERE batch_id = ? GROUP BY status",
                        (batch["id"],),
                    ).fetchall()
                    count_by_status = {row["status"]: row["count"] for row in counts}
                    completed = count_by_status.get(JobStatus.COMPLETED.value, 0)
                    failed = count_by_status.get(JobStatus.FAILED.value, 0)
                    if completed == len(rows):
                        final_status = BatchStatus.COMPLETED
                    elif completed:
                        final_status = BatchStatus.PARTIAL
                    else:
                        final_status = BatchStatus.FAILED
                connection.execute(
                    "UPDATE batches SET status = ?, result_summary = ?, updated_at = ? WHERE id = ?",
                    (final_status.value, json.dumps({"completed": completed, "failed": failed, "recovered": True}),
                     _utc_now(), batch["id"]),
                )
                recovered += 1
        return recovered

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
            current_status = JobStatus(row["status"])
            if status not in _ALLOWED_JOB_TRANSITIONS[current_status]:
                raise ValueError(f"Invalid job transition: {current_status.value} -> {status.value}")
            connection.execute(
                "UPDATE jobs SET status = ?, current_stage = ?, error_summary = ?, updated_at = ?, "
                "progress = CASE WHEN ? THEN 1 ELSE progress END WHERE id = ?",
                (status.value, stage, error_summary, now, status is JobStatus.COMPLETED, str(job_id)),
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

    def transition_batch(
        self,
        batch_id: UUID,
        status: BatchStatus,
        result_summary: str | None = None,
    ) -> None:
        with self._connection() as connection:
            existing = connection.execute("SELECT status FROM batches WHERE id = ?", (str(batch_id),)).fetchone()
            if existing is None:
                raise AdapterError("Batch was not found", FailureCategory.NOT_FOUND)
            current_status = BatchStatus(existing["status"])
            if status not in _ALLOWED_BATCH_TRANSITIONS[current_status]:
                raise ValueError(f"Invalid batch transition: {current_status.value} -> {status.value}")
            cursor = connection.execute(
                "UPDATE batches SET status = ?, result_summary = ?, updated_at = ? WHERE id = ?",
                (status.value, result_summary, _utc_now(), str(batch_id)),
            )
            assert cursor.rowcount == 1

    def set_job_progress(self, job_id: UUID, progress: float) -> None:
        """Raise the job's progress (0..1); it never moves backwards. Not a job event."""
        with self._connection() as connection:
            connection.execute(
                "UPDATE jobs SET progress = ? WHERE id = ? AND progress < ?",
                (min(1.0, progress), str(job_id), min(1.0, progress)),
            )

    def record_retry(self, job_id: UUID, attempt: int) -> None:
        with self._connection() as connection:
            row = connection.execute("SELECT batch_id, owner_id FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
            if row is None:
                raise AdapterError("Job was not found", FailureCategory.NOT_FOUND)
            connection.execute(
                "UPDATE jobs SET retry_count = retry_count + 1, updated_at = ? WHERE id = ?",
                (_utc_now(), str(job_id)),
            )
            connection.execute(
                """INSERT INTO job_events
                   (id, batch_id, job_id, name, occurred_at, details_json, actor_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()), row["batch_id"], str(job_id), "retry_scheduled", _utc_now(),
                    json.dumps({"attempt": attempt}), row["owner_id"],
                ),
            )

    def get_job(self, job_id: UUID, owner_id: str) -> JobView | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ? AND owner_id = ?",
                (str(job_id), owner_id),
            ).fetchone()
        return self._job_view(row) if row else None

    def list_jobs_for_batch(self, batch_id: UUID, owner_id: str) -> list[JobView]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE batch_id = ? AND owner_id = ? ORDER BY rowid",
                (str(batch_id), owner_id),
            ).fetchall()
        return [self._job_view(row) for row in rows]

    def update_project_source(self, job_id: UUID, source_id: str, title: str) -> None:
        with self._connection() as connection:
            result = connection.execute(
                "UPDATE projects SET source_id = ?, title = ?, updated_at = ? "
                "WHERE id = (SELECT project_id FROM jobs WHERE id = ?)",
                (source_id, title, _utc_now(), str(job_id)),
            )
            if result.rowcount != 1:
                raise AdapterError("Project job was not found", FailureCategory.NOT_FOUND)

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

    def storage_keys_in_use(self) -> set[str]:
        """Every file a project still owns, plus files whose deletion is still pending."""
        with self._connection() as connection:
            keys = {row[0] for row in connection.execute("SELECT storage_key FROM media_assets")}
            for (pending,) in connection.execute("SELECT storage_keys_json FROM pending_project_deletions"):
                keys.update(json.loads(pending))
        return keys

    def has_active_jobs(self) -> bool:
        with self._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM jobs WHERE status NOT IN (?, ?, ?)",
                (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value),
            ).fetchone() is not None

    def list_assets(self, job_id: UUID, owner_id: str) -> list[MediaAsset]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT a.* FROM media_assets a JOIN jobs j ON j.id = a.job_id
                   WHERE a.job_id = ? AND j.owner_id = ? ORDER BY a.created_at, a.rowid""",
                (str(job_id), owner_id),
            ).fetchall()
        return [self._asset(row) for row in rows]

    def get_asset(self, asset_id: UUID, owner_id: str) -> MediaAsset | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT a.* FROM media_assets a JOIN jobs j ON j.id = a.job_id "
                "WHERE a.id = ? AND j.owner_id = ?",
                (str(asset_id), owner_id),
            ).fetchone()
        return self._asset(row) if row else None

    def get_waveform(self, asset_id: UUID) -> dict[str, object] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT waveform_json FROM media_assets WHERE id = ?", (str(asset_id),)).fetchone()
        return json.loads(row["waveform_json"]) if row and row["waveform_json"] else None

    def save_waveform(self, asset_id: UUID, data: dict[str, object]) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE media_assets SET waveform_json = ? WHERE id = ?", (json.dumps(data), str(asset_id))
            )

    @staticmethod
    def _project(row: sqlite3.Row) -> ProjectRecord:
        return ProjectRecord(
            project_id=UUID(row["id"]), owner_id=row["owner_id"],
            input_type=SourceKind(row["input_type"]), input_value=row["input_value"],
            source_id=row["source_id"], title=row["title"],
            created_at=_parse_datetime(row["created_at"]),
            job_id=UUID(row["job_id"]), job_status=row["job_status"], job_progress=row["job_progress"],
        )

    def get_project(self, project_id: UUID, owner_id: str) -> ProjectRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT p.*, j.id AS job_id, j.status AS job_status, j.progress AS job_progress "
                "FROM projects p JOIN jobs j ON j.project_id = p.id "
                "WHERE p.id = ? AND p.owner_id = ?",
                (str(project_id), owner_id),
            ).fetchone()
        return self._project(row) if row else None

    def list_projects(self, owner_id: str) -> list[ProjectRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT p.*, j.id AS job_id, j.status AS job_status, j.progress AS job_progress "
                "FROM projects p JOIN jobs j ON j.project_id = p.id "
                "WHERE p.owner_id = ? ORDER BY p.created_at, p.rowid",
                (owner_id,),
            ).fetchall()
        return [self._project(row) for row in rows]

    def begin_project_deletion(self, project_id: UUID, owner_id: str) -> ProjectDeletion:
        """Persist managed file keys, then remove project/job metadata atomically."""
        with self._connection() as connection:
            pending = connection.execute(
                "SELECT storage_keys_json FROM pending_project_deletions WHERE project_id = ? AND owner_id = ?",
                (str(project_id), owner_id),
            ).fetchone()
            if pending:
                return ProjectDeletion(project_id, owner_id, tuple(json.loads(pending["storage_keys_json"])))

            row = connection.execute(
                """SELECT p.id, p.title, p.source_id, j.id AS job_id, j.status AS job_status, j.batch_id,
                          b.status AS batch_status,
                          (SELECT COUNT(*) FROM jobs sibling WHERE sibling.batch_id = j.batch_id) AS batch_size
                   FROM projects p JOIN jobs j ON j.project_id = p.id
                   JOIN batches b ON b.id = j.batch_id
                   WHERE p.id = ? AND p.owner_id = ?""",
                (str(project_id), owner_id),
            ).fetchone()
            if row is None:
                raise AdapterError("Project was not found", FailureCategory.NOT_FOUND)
            if row["batch_status"] == BatchStatus.PROCESSING.value:
                raise AdapterError("Project batch is currently processing", FailureCategory.CONFLICT)
            if row["job_status"] == JobStatus.QUEUED.value:
                if row["batch_status"] != BatchStatus.QUEUED.value or row["batch_size"] != 1:
                    raise AdapterError("Queued multi-job batches cannot be partially deleted", FailureCategory.CONFLICT)
                now = _utc_now()
                connection.execute(
                    "UPDATE jobs SET status = ?, current_stage = ?, updated_at = ? WHERE id = ?",
                    (JobStatus.CANCELLED.value, "project_deletion", now, row["job_id"]),
                )
                connection.execute(
                    "UPDATE batches SET status = ?, updated_at = ? WHERE id = ?",
                    (BatchStatus.CANCELLED.value, now, row["batch_id"]),
                )
            elif row["job_status"] not in {
                JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value,
            }:
                raise AdapterError("Project job is active", FailureCategory.CONFLICT)

            # Files another project also uses (a reused source or stem set) stay for that project.
            keys = tuple(dict.fromkeys(item["storage_key"] for item in connection.execute(
                """SELECT a.storage_key FROM media_assets a WHERE a.job_id = ? AND NOT EXISTS (
                       SELECT 1 FROM media_assets other WHERE other.storage_key = a.storage_key AND other.job_id != a.job_id)
                   ORDER BY a.created_at, a.rowid""",
                (row["job_id"],),
            ).fetchall()))
            connection.execute(
                "INSERT INTO pending_project_deletions(project_id, owner_id, storage_keys_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (str(project_id), owner_id, json.dumps(keys), _utc_now()),
            )
            connection.execute("DELETE FROM projects WHERE id = ? AND owner_id = ?", (str(project_id), owner_id))
            return ProjectDeletion(project_id, owner_id, keys, row["title"], row["source_id"])

    def pending_project_deletions(self) -> list[ProjectDeletion]:
        """Deletions whose files are not all removed yet (for example, a file was locked)."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT project_id, owner_id, storage_keys_json FROM pending_project_deletions WHERE storage_keys_json != '[]'"
            ).fetchall()
        return [ProjectDeletion(UUID(r["project_id"]), r["owner_id"], tuple(json.loads(r["storage_keys_json"]))) for r in rows]

    def source_is_busy(self, source_id: str) -> bool:
        """True while any unfinished job works on this source (it may be using the shared temp folder)."""
        with self._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM jobs j JOIN projects p ON p.id = j.project_id WHERE p.source_id = ? AND j.status NOT IN (?, ?, ?)",
                (source_id, JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value),
            ).fetchone() is not None

    def shared_storage_keys(self, job_id: UUID) -> set[str]:
        """Storage keys of this job that another job also references."""
        with self._connection() as connection:
            return {row[0] for row in connection.execute(
                """SELECT DISTINCT a.storage_key FROM media_assets a JOIN media_assets other
                   ON other.storage_key = a.storage_key AND other.job_id != a.job_id WHERE a.job_id = ?""",
                (str(job_id),),
            )}

    def complete_project_deletion(self, project_id: UUID, owner_id: str) -> None:
        with self._connection() as connection:
            # Keep an empty-key receipt so repeated DELETE calls remain idempotent.
            connection.execute(
                "UPDATE pending_project_deletions SET storage_keys_json = '[]' "
                "WHERE project_id = ? AND owner_id = ?",
                (str(project_id), owner_id),
            )

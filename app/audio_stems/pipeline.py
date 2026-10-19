"""Batch orchestration (download-first phases, persisted states) and the database-backed worker loop."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Callable, Sequence
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from audio_stems.models import (
    AdapterError,
    BatchReceipt,
    BatchStatus,
    DownloadedAudio,
    FailureCategory,
    JobStatus,
    JobView,
    MediaAssetType,
    SeparationOptions,
    SourceKind,
    SourceRequest,
    StemArtifact,
    VideoCandidate,
)
from audio_stems.config import Settings
import threading
import time
from typing import Callable
from audio_stems.models import SeparationOptions

from audio_stems.separator import BEST_MODEL, audio_duration

if TYPE_CHECKING:
    from audio_stems.db import SqliteMetadataStore
    from audio_stems.separator import StemSeparator
    from audio_stems.storage import LocalMediaStorage
    from audio_stems.youtube import PytubeDownloader


logger = logging.getLogger("audio_stems.jobs")


@dataclass(frozen=True)
class BatchProcessingResult:
    batch_id: UUID
    status: BatchStatus
    jobs: tuple[JobView, ...]


def _video_id(url: str) -> str:
    parsed = urlparse(url)
    query_id = parse_qs(parsed.query).get("v", [None])[0]
    if query_id:
        return query_id
    path_parts = [part for part in parsed.path.split("/") if part]
    if parsed.hostname and parsed.hostname.lower() in {"youtu.be", "www.youtu.be"} and path_parts:
        return path_parts[0]
    if len(path_parts) == 2 and path_parts[0].lower() in {"shorts", "embed", "live", "v"}:
        return path_parts[1]
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


_ORIGINAL_MODEL = "htdemucs_6s"


def _safe_error_summary(error: AdapterError) -> str:
    if error.category is FailureCategory.DISK_FULL:
        return str(error)  # our own message with the space numbers; it carries no paths
    return {
        FailureCategory.TRANSIENT: "Temporary service error",
        FailureCategory.INVALID_INPUT: "Input is invalid",
        FailureCategory.NOT_FOUND: "Source was not found",
        FailureCategory.CONFLICT: "Processing conflict",
        FailureCategory.PERMANENT: "Processing failed permanently",
    }[error.category]


class BatchOrchestrator:
    def __init__(
        self,
        *,
        repository: SqliteMetadataStore,
        downloader: PytubeDownloader,
        separator: StemSeparator,
        storage: LocalMediaStorage,
        options: SeparationOptions,
        settings: Settings,
        sleep: Callable[[float], None],
    ) -> None:
        self.repository = repository
        self.downloader = downloader
        self.separator = separator
        self.storage = storage
        self.options = options
        self.settings = settings
        self.sleep = sleep
        self._progress_written: dict[UUID, tuple[float, float]] = {}

    def process_batch(
        self,
        receipt: BatchReceipt,
        sources: Sequence[SourceRequest],
        owner_id: str | None = None,
        *,
        already_processing: bool = False,
    ) -> BatchProcessingResult:
        if len(receipt.job_ids) != len(sources) or not sources:
            raise AdapterError("Batch source list does not match its jobs", FailureCategory.INVALID_INPUT)
        owner_id = owner_id or receipt.owner_id
        if not owner_id:
            raise AdapterError("Batch owner context is required", FailureCategory.INVALID_INPUT)

        if not already_processing:
            self.repository.transition_batch(receipt.batch_id, BatchStatus.PROCESSING)
        downloaded: dict[UUID, DownloadedAudio] = {}

        # Phase one: attempt resolution and download for every item.
        for job_id, request in zip(receipt.job_ids, sources):
            try:
                candidate = self._resolve_source(job_id, request, owner_id, receipt.batch_id)
                current = self.repository.get_job(job_id, owner_id)
                if current is None:
                    raise AdapterError("Job was not found", FailureCategory.NOT_FOUND)
                if current.status in {JobStatus.QUEUED, JobStatus.SEARCHING}:
                    self.repository.transition_job(job_id, JobStatus.DOWNLOADING, stage="download")

                audio = self._retry(job_id, lambda: self.storage.find_existing_audio(candidate), owner_id, receipt.batch_id)
                if audio is None:
                    audio = self._retry(
                        job_id,
                        lambda: self.downloader.download(candidate, progress=self._progress_reporter(job_id, 0.02, 0.10)),
                        owner_id, receipt.batch_id,
                    )
                else:
                    logger.info(
                        "Existing audio reused",
                        extra={"event": "audio_download_reused", "ClientID": owner_id,
                               "batch_id": str(receipt.batch_id), "job_id": str(job_id)},
                    )
                if request.kind is SourceKind.VIDEO_URL and audio.source.url != request.value:
                    raise AdapterError("Downloader returned a different video", FailureCategory.PERMANENT)
                self.repository.update_project_source(job_id, audio.source.source_id, audio.source.title)
                asset = self._retry(
                    job_id, lambda: self.storage.save_audio(job_id, audio), owner_id, receipt.batch_id
                )
                self.repository.add_asset(asset)
                seconds = audio_duration(audio.path)  # the length tells how long the separation will take
                if seconds:
                    self.repository.set_audio_seconds(job_id, seconds)
                self.repository.transition_job(job_id, JobStatus.DOWNLOADED, stage="download")
                self._report_progress(job_id, 0.10, force=True)
                downloaded[job_id] = audio
            except AdapterError as error:
                self._fail(job_id, error, "download", owner_id, receipt.batch_id)
            except Exception:
                logger.exception(
                    "Unexpected job failure during download",
                    extra={"event": "audio_download_failed", "ClientID": owner_id,
                           "batch_id": str(receipt.batch_id), "job_id": str(job_id)},
                )
                self._fail(
                    job_id, AdapterError("Processing failed", FailureCategory.PERMANENT),
                    "download", owner_id, receipt.batch_id,
                )

        # Phase two: only starts after all phase-one attempts have finished.
        for job_id, request in zip(receipt.job_ids, sources):
            audio = downloaded.get(job_id)
            if audio is None:
                continue
            try:
                job = self.repository.get_job(job_id, owner_id)
                options = (job.separation_options if job else None) or receipt.separation_options or self.options
                self._separate(job_id, audio, owner_id, receipt.batch_id, options)
            except AdapterError as error:
                self._fail(job_id, error, "separation", owner_id, receipt.batch_id)
            except Exception:
                logger.exception(
                    "Unexpected job failure during separation",
                    extra={"event": "separation_failed", "ClientID": owner_id,
                           "batch_id": str(receipt.batch_id), "job_id": str(job_id)},
                )
                self._fail(
                    job_id, AdapterError("Processing failed", FailureCategory.PERMANENT),
                    "separation", owner_id, receipt.batch_id,
                )

        jobs = tuple(
            self.repository.get_job(job_id, owner_id)
            for job_id in receipt.job_ids
        )
        final_jobs = tuple(job for job in jobs if job is not None)
        completed = sum(job.status is JobStatus.COMPLETED for job in final_jobs)
        failed = sum(job.status is JobStatus.FAILED for job in final_jobs)
        if len(final_jobs) == len(receipt.job_ids) and completed == len(final_jobs):
            batch_status = BatchStatus.COMPLETED
        elif len(final_jobs) != len(receipt.job_ids) or failed == len(final_jobs):
            batch_status = BatchStatus.FAILED
        else:
            batch_status = BatchStatus.PARTIAL
        summary = json.dumps({"completed": completed, "failed": failed})
        self.repository.transition_batch(receipt.batch_id, batch_status, summary)
        return BatchProcessingResult(receipt.batch_id, batch_status, final_jobs)

    def _report_progress(self, job_id: UUID, value: float, *, force: bool = False) -> None:
        """Persist progress at most every 0.5 s and only after a 1 % change: SQLite commits are slow."""
        last_value, last_time = self._progress_written.get(job_id, (0.0, 0.0))
        now = time.monotonic()
        if not force and (value - last_value < 0.01 or now - last_time < 0.5):
            return
        self._progress_written[job_id] = (value, now)
        try:
            self.repository.set_job_progress(job_id, value)
        except Exception:
            logger.warning("Job progress could not be saved", extra={"event": "job_progress_failed", "job_id": str(job_id)})

    def _progress_reporter(self, job_id: UUID, start: float, end: float) -> Callable[[float], None]:
        """Map a stage's own 0..1 progress onto its share of the job."""
        return lambda fraction: self._report_progress(job_id, start + (end - start) * min(1.0, max(0.0, fraction)))

    def _resolve_source(
        self, job_id: UUID, request: SourceRequest, owner_id: str, batch_id: UUID
    ) -> VideoCandidate:
        current = self.repository.get_job(job_id, owner_id)
        if current is None:
            raise AdapterError("Job was not found", FailureCategory.NOT_FOUND)
        if request.kind is SourceKind.SEARCH_QUERY:
            self.repository.transition_job(job_id, JobStatus.SEARCHING, stage="search")
            candidates = self._retry(
                job_id, lambda: self.downloader.search(request.value), owner_id, batch_id
            )
            if not candidates:
                raise AdapterError("No source candidates found", FailureCategory.NOT_FOUND)
            return candidates[0]

        video_id = _video_id(request.value)
        return VideoCandidate(video_id, video_id, request.value)

    def _retry(
        self,
        job_id: UUID,
        operation: Callable[[], object],
        owner_id: str,
        batch_id: UUID,
    ):
        for attempt in range(1, self.settings.retry_count + 1):
            try:
                return operation()
            except AdapterError as error:
                if not error.retryable or attempt >= self.settings.retry_count:
                    raise
                next_attempt = attempt + 1
                self.repository.record_retry(job_id, next_attempt)
                logger.warning(
                    "Transient operation will be retried",
                    extra={"event": "job_retry_scheduled", "ClientID": owner_id,
                           "batch_id": str(batch_id), "job_id": str(job_id), "attempt": next_attempt},
                )
                delay_index = attempt - 1
                if delay_index < len(self.settings.retry_delays):
                    self.sleep(self.settings.retry_delays[delay_index])
        raise AssertionError("Retry loop ended without a result")

    def _separate(
        self, job_id: UUID, audio: DownloadedAudio, owner_id: str, batch_id: UUID,
        options: SeparationOptions,
    ) -> None:
        title = audio.source.title
        source_id = audio.source.source_id
        stems = self.separator.model_stems(options.model_name)
        # The original model keeps the pre-version-10 folder; other models get "<title> (<model>)".
        variant = None if options.model_name == _ORIGINAL_MODEL else options.model_name
        existing = self._retry(
            job_id, lambda: self.storage.existing_stems(title, stems, source_id, variant), owner_id, batch_id
        )
        if existing:
            for stem_name, path in existing.items():
                asset = self._retry(
                    job_id,
                    lambda stem_name=stem_name, path=path: self.storage.save_stem(
                        job_id, title, source_id, StemArtifact(stem_name, path), variant
                    ),
                    owner_id,
                    batch_id,
                )
                self.repository.add_asset(asset)
            self.repository.transition_job(job_id, JobStatus.COMPLETED, stage="completed")
            return

        self.repository.transition_job(job_id, JobStatus.SEPARATING, stage="separation")
        temp_dir = self.storage.temporary_directory(title, source_id)
        result = self._retry(
            job_id,
            lambda: self.separator.separate(
                audio.path, temp_dir, options, progress=self._progress_reporter(job_id, 0.10, 0.97)
            ),
            owner_id,
            batch_id,
        )
        artifacts = _checked_artifacts(result.stems, stems)
        # Not retried: it deletes each float stem once its FLAC is written, to need less temporary space.
        for asset in self.storage.save_stems(job_id, title, source_id, artifacts, result.level, variant):
            self.repository.add_asset(asset)
        if not self._retry(
            job_id, lambda: self.storage.stems_complete(title, stems, source_id, variant), owner_id, batch_id
        ):
            raise AdapterError("Stored stem set is incomplete", FailureCategory.PERMANENT)
        self.storage.remove_temporary_directory(title, source_id)
        self.repository.transition_job(job_id, JobStatus.COMPLETED, stage="completed")

    def _fail(
        self, job_id: UUID, error: AdapterError, stage: str, owner_id: str, batch_id: UUID
    ) -> None:
        try:
            job = self.repository.get_job(job_id, owner_id)
            if job and job.status not in {JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.COMPLETED}:
                event_name = "audio_download_failed" if stage == "download" else "separation_failed"
                logger.exception(
                    "Job processing failed",
                    extra={"event": event_name, "ClientID": owner_id, "batch_id": str(batch_id),
                           "job_id": str(job_id), "error_type": type(error).__name__},
                )
                self.repository.transition_job(
                    job_id,
                    JobStatus.FAILED,
                    stage=stage,
                    error_summary=_safe_error_summary(error),
                )
        except Exception:
            logger.exception(
                "Unable to persist failed job state",
                extra={"event": "job_state_persist_failed", "ClientID": owner_id,
                       "batch_id": str(batch_id), "job_id": str(job_id)},
            )


    def upgrade_project(
        self, project_id: UUID, owner_id: str, progress: Callable[[float], None] | None = None,
    ) -> tuple[str, ...]:
        """«Улучшить качество»: separate a finished project again with the best model and switch it to the
        new stems. The old stems stay playable until the switch; returns the old files no project uses any
        more (deleted by the caller once the player has let go of them)."""
        project = self.repository.get_project(project_id, owner_id)
        job = self.repository.get_job(project.job_id, owner_id) if project else None
        if job is None or job.status is not JobStatus.COMPLETED:
            raise AdapterError("Улучшить можно только завершённый проект", FailureCategory.PERMANENT)
        if job.separation_options and job.separation_options.model_name == BEST_MODEL:
            return ()
        options = SeparationOptions(BEST_MODEL, self.options.device)
        source = next((a for a in self.repository.list_assets(job.job_id, owner_id)
                       if a.asset_type is MediaAssetType.SOURCE_AUDIO), None)
        audio_path = self.storage.resolve_path(source.storage_key) if source else None
        if audio_path is None or not audio_path.is_file():
            raise AdapterError("Исходное аудио проекта не найдено на диске", FailureCategory.PERMANENT)
        title, source_id = project.title or project.input_value, project.source_id
        stems = self.separator.model_stems(BEST_MODEL)
        existing = self.storage.existing_stems(title, stems, source_id, BEST_MODEL)
        seconds = None
        if existing:  # another project of the same song already has them
            assets = [self.storage.save_stem(job.job_id, title, source_id, StemArtifact(name, path), BEST_MODEL)
                      for name, path in existing.items()]
        else:
            started = time.monotonic()
            temp_dir = self.storage.temporary_directory(title, source_id)
            result = self.separator.separate(audio_path, temp_dir, options, progress=progress)
            artifacts = _checked_artifacts(result.stems, stems)
            assets = self.storage.save_stems(job.job_id, title, source_id, artifacts, result.level, BEST_MODEL)
            if not self.storage.stems_complete(title, stems, source_id, BEST_MODEL):
                raise AdapterError("Stored stem set is incomplete", FailureCategory.PERMANENT)
            self.storage.remove_temporary_directory(title, source_id)
            seconds = time.monotonic() - started
        return self.repository.replace_project_stems(job.job_id, assets, options, seconds)


def _checked_artifacts(results: Sequence[StemArtifact], stems: Sequence[str]) -> list[StemArtifact]:
    """The separator's stems in the model's order, or an error when one is missing or empty."""
    stem_map = {stem.name: stem for stem in results}
    if any(name not in stem_map for name in stems):
        raise AdapterError("Separator returned incomplete output", FailureCategory.PERMANENT)
    artifacts = [stem_map[stem_name] for stem_name in stems]
    if any(not artifact.path.is_file() or artifact.path.stat().st_size == 0 for artifact in artifacts):
        raise AdapterError("Separator returned an invalid stem", FailureCategory.PERMANENT)
    return artifacts


worker_logger = logging.getLogger("audio_stems.worker")


class DatabaseWorker:
    def __init__(
        self,
        repository: SqliteMetadataStore,
        orchestrator: BatchOrchestrator,
        *,
        poll_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.repository = repository
        self.orchestrator = orchestrator
        self.poll_seconds = poll_seconds
        self.sleep = sleep

    def recover_on_start(self) -> int:
        self.repository.requeue_interrupted_analyses("upgrade")
        count = self.repository.recover_interrupted_batches()
        if count:
            worker_logger.warning(
                "Interrupted batches were marked failed; their downloaded audio remains reusable",
                extra={"event": "worker_batches_recovered", "recovered_batches": count},
            )
        return count

    def run_once(self) -> bool:
        claimed = self.repository.claim_next_batch()
        if claimed is None:
            return self.run_upgrade()  # one heavy separation at a time: upgrades wait for batches
        try:
            self.orchestrator.process_batch(
                claimed.receipt,
                claimed.sources,
                already_processing=True,
            )
        except Exception:
            worker_logger.exception(
                "Worker failed while processing a batch",
                extra={"event": "worker_batch_failed", "batch_id": str(claimed.receipt.batch_id)},
            )
            self.repository.recover_interrupted_batches()
            return False
        return True

    def run_upgrade(self, *, delete_attempts: int = 20, delete_pause: float = 0.5) -> bool:
        """Work one queued «Улучшить качество»; True when one ran."""
        task = self.repository.claim_analysis("upgrade")
        if task is None:
            return False
        project_id, request = task
        reported = [0.0]

        def progress(value: float) -> None:
            if value - reported[0] >= 0.01:
                reported[0] = value
                self.repository.set_analysis_progress(project_id, "upgrade", min(0.99, value))

        try:
            replaced = self.orchestrator.upgrade_project(UUID(project_id), request["owner_id"], progress)
        except AdapterError as error:
            self.repository.fail_analysis(project_id, "upgrade", str(error))
            return True
        except Exception:
            worker_logger.exception("Quality upgrade failed", extra={"event": "upgrade_failed", "project_id": project_id})
            self.repository.fail_analysis(project_id, "upgrade", "Не удалось улучшить качество")
            return True
        self.repository.finish_analysis(project_id, "upgrade", {"model_name": BEST_MODEL})
        # The UI switches to the new stems within a second; until then Windows keeps the old ones locked.
        for key in replaced:
            try:
                self.orchestrator.storage.delete_asset(key, attempts=delete_attempts, pause=delete_pause)
            except AdapterError:
                worker_logger.warning("An old stem is still locked; Settings → cleanup removes it later",
                                      extra={"event": "upgrade_cleanup_pending", "project_id": project_id})
        return True

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        stop_event = stop_event or threading.Event()
        self.recover_on_start()
        worker_logger.info("Worker started", extra={"event": "worker_started"})
        while not stop_event.is_set():
            if not self.run_once():
                stop_event.wait(self.poll_seconds)


def build_worker(settings: Settings | None = None) -> DatabaseWorker:
    """Compose local adapters only in the worker process entry point."""
    from audio_stems.separator import StemSeparator
    from audio_stems.storage import LocalMediaStorage
    from audio_stems.db import SqliteMetadataStore
    from audio_stems.youtube import PytubeDownloader

    settings = settings or Settings.load()
    if not settings.database_url.startswith("sqlite:///"):
        raise ValueError("The local worker currently supports SQLite database URLs only")
    database_path = settings.database_url.removeprefix("sqlite:///")
    repository = SqliteMetadataStore(database_path)
    storage = LocalMediaStorage(
        settings.storage_root,
        temp_root=settings.temp_root,
    )
    separator = StemSeparator(settings.storage_root.parent / "models")
    downloader = PytubeDownloader(storage, settings)
    options = SeparationOptions(settings.model_name, separator.resolve_device(settings.model_device))
    orchestrator = BatchOrchestrator(
        repository=repository,
        downloader=downloader,
        separator=separator,
        storage=storage,
        options=options,
        settings=settings,
        sleep=time.sleep,
    )
    return DatabaseWorker(repository, orchestrator)


if __name__ == "__main__":
    from audio_stems.config import configure_logging

    configure_logging(Settings.load())
    try:
        build_worker().run_forever()
    except KeyboardInterrupt:
        worker_logger.info("Worker stopped", extra={"event": "worker_stopped"})

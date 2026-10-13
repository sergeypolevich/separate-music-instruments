"""Batch job orchestration with persisted states and download-first phases."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from contracts import (
    AdapterError,
    BatchReceipt,
    BatchStatus,
    DownloadedAudio,
    Downloader,
    FailureCategory,
    JobRepository,
    JobStatus,
    JobView,
    MediaStorage,
    SeparationOptions,
    SourceKind,
    SourceRequest,
    StemArtifact,
    StemSeparator,
    VideoCandidate,
)
from settings import Settings


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


def _safe_error_summary(error: AdapterError) -> str:
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
        repository: JobRepository,
        downloader: Downloader,
        separator: StemSeparator,
        storage: MediaStorage,
        options: SeparationOptions,
        expected_stems: Sequence[str],
        settings: Settings,
        sleep: Callable[[float], None],
    ) -> None:
        self.repository = repository
        self.downloader = downloader
        self.separator = separator
        self.storage = storage
        self.options = options
        self.expected_stems = tuple(expected_stems)
        self.settings = settings
        self.sleep = sleep

    def submit_batch(
        self,
        owner_id: str,
        sources: Sequence[SourceRequest],
        idempotency_key: str | None = None,
        separation_options: SeparationOptions | None = None,
    ) -> BatchReceipt:
        options = separation_options or self.options
        return self.repository.create_batch(owner_id, sources, idempotency_key, options)

    def process_batch(
        self,
        receipt: BatchReceipt,
        sources: Sequence[SourceRequest],
        owner_id: str | None = None,
    ) -> BatchProcessingResult:
        if len(receipt.job_ids) != len(sources) or not sources:
            raise AdapterError("Batch source list does not match its jobs", FailureCategory.INVALID_INPUT)
        owner_id = owner_id or receipt.owner_id
        if not owner_id:
            raise AdapterError("Batch owner context is required", FailureCategory.INVALID_INPUT)

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
                    audio = self._retry(job_id, lambda: self.downloader.download(candidate), owner_id, receipt.batch_id)
                else:
                    logger.info(
                        "Existing audio reused",
                        extra={"event": "audio_download_reused", "ClientID": owner_id,
                               "batch_id": str(receipt.batch_id), "job_id": str(job_id)},
                    )
                if request.kind is SourceKind.VIDEO_URL and audio.source.url != request.value:
                    raise AdapterError("Downloader returned a different video", FailureCategory.PERMANENT)
                asset = self._retry(
                    job_id, lambda: self.storage.save_audio(job_id, audio), owner_id, receipt.batch_id
                )
                self.repository.add_asset(asset)
                self.repository.transition_job(job_id, JobStatus.DOWNLOADED, stage="download")
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
        if self._retry(
            job_id, lambda: self.storage.stems_complete(title, self.expected_stems, source_id), owner_id, batch_id
        ):
            for stem_name in self.expected_stems:
                target = self.storage.stem_file_target(title, stem_name, source_id)
                asset = self._retry(
                    job_id,
                    lambda stem_name=stem_name, target=target: self.storage.save_stem(
                        job_id, title, source_id, StemArtifact(stem_name, target.path)
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
            lambda: self.separator.separate(audio.path, temp_dir, options),
            owner_id,
            batch_id,
        )
        stem_map = {stem.name: stem for stem in result.stems}
        if any(name not in stem_map for name in self.expected_stems):
            raise AdapterError("Separator returned incomplete output", FailureCategory.PERMANENT)
        for stem_name in self.expected_stems:
            artifact = stem_map[stem_name]
            if not artifact.path.is_file() or artifact.path.stat().st_size == 0:
                raise AdapterError("Separator returned an invalid stem", FailureCategory.PERMANENT)
            asset = self._retry(
                job_id,
                lambda artifact=artifact: self.storage.save_stem(job_id, title, source_id, artifact),
                owner_id,
                batch_id,
            )
            self.repository.add_asset(asset)
        if not self._retry(
            job_id, lambda: self.storage.stems_complete(title, self.expected_stems, source_id), owner_id, batch_id
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

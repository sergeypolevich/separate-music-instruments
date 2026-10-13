"""Database-backed worker loop. Run with ``py worker.py`` for local processing."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from contracts import JobRepository, SeparationOptions
from jobs import BatchOrchestrator
from settings import Settings


logger = logging.getLogger("audio_stems.worker")


class DatabaseWorker:
    def __init__(
        self,
        repository: JobRepository,
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
        count = self.repository.recover_interrupted_batches()
        if count:
            logger.warning(
                "Interrupted batches were marked failed; their downloaded audio remains reusable",
                extra={"event": "worker_batches_recovered", "recovered_batches": count},
            )
        return count

    def run_once(self) -> bool:
        claimed = self.repository.claim_next_batch()
        if claimed is None:
            return False
        try:
            self.orchestrator.process_batch(
                claimed.receipt,
                claimed.sources,
                already_processing=True,
            )
        except Exception:
            logger.exception(
                "Worker failed while processing a batch",
                extra={"event": "worker_batch_failed", "batch_id": str(claimed.receipt.batch_id)},
            )
            self.repository.recover_interrupted_batches()
            return False
        return True

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        stop_event = stop_event or threading.Event()
        self.recover_on_start()
        logger.info("Worker started", extra={"event": "worker_started"})
        while not stop_event.is_set():
            if not self.run_once():
                stop_event.wait(self.poll_seconds)


def build_worker(settings: Settings | None = None) -> DatabaseWorker:
    """Compose local adapters only in the worker process entry point."""
    from demucs_adapter import DemucsSeparator
    from local_storage import LocalMediaStorage
    from metadata import SqliteMetadataStore
    from pytube_adapter import PytubeDownloader

    settings = settings or Settings.load()
    if not settings.database_url.startswith("sqlite:///"):
        raise ValueError("The local worker currently supports SQLite database URLs only")
    database_path = settings.database_url.removeprefix("sqlite:///")
    repository = SqliteMetadataStore(database_path)
    storage = LocalMediaStorage(
        settings.storage_root,
        temp_root=settings.temp_root,
    )
    separator = DemucsSeparator()
    downloader = PytubeDownloader(storage, settings)
    options = SeparationOptions(settings.model_name, settings.model_device)
    orchestrator = BatchOrchestrator(
        repository=repository,
        downloader=downloader,
        separator=separator,
        storage=storage,
        options=options,
        expected_stems=("vocals", "drums", "bass", "guitar", "piano", "other"),
        settings=settings,
        sleep=time.sleep,
    )
    return DatabaseWorker(repository, orchestrator)


if __name__ == "__main__":
    from logging_setup import configure_logging

    configure_logging(Settings.load())
    try:
        build_worker().run_forever()
    except KeyboardInterrupt:
        logger.info("Worker stopped", extra={"event": "worker_stopped"})

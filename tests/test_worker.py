from dataclasses import replace

from contracts import BatchStatus, JobStatus, SeparationOptions, SourceKind, SourceRequest
from demucs_adapter import DemucsSeparator
from metadata import SqliteMetadataStore
from tests import test_jobs as job_fixtures
from pytube_adapter import PytubeDownloader
from settings import Settings
from worker import DatabaseWorker, build_worker


class FakeOrchestrator:
    def __init__(self, store, *, fail=False):
        self.store = store
        self.fail = fail
        self.calls = []

    def process_batch(self, receipt, sources, *, already_processing=False):
        self.calls.append((receipt, tuple(sources), already_processing))
        if self.fail:
            raise RuntimeError("worker crashed")
        return None


def test_worker_claims_persisted_batch_and_passes_saved_inputs_and_options(tmp_path):
    store = SqliteMetadataStore(tmp_path / "jobs.db")
    requests = [
        SourceRequest(SourceKind.SEARCH_QUERY, "Track one"),
        SourceRequest(SourceKind.VIDEO_URL, "https://youtube.com/watch?v=video-2"),
    ]
    selected = SeparationOptions("htdemucs", "cpu", {"shifts": 2})
    store.create_batch("client-1", requests, separation_options=selected)
    orchestrator = FakeOrchestrator(store)

    assert DatabaseWorker(store, orchestrator).run_once() is True

    receipt, restored_sources, already_processing = orchestrator.calls[0]
    assert restored_sources == tuple(requests)
    assert receipt.separation_options == selected
    assert already_processing is True
    assert store.get_batch(receipt.batch_id, "client-1").status == BatchStatus.PROCESSING.value


def test_worker_exception_is_recovered_to_failed_without_claiming_as_resumable(tmp_path):
    store = SqliteMetadataStore(tmp_path / "jobs.db")
    requests = [SourceRequest(SourceKind.SEARCH_QUERY, "Track")]
    receipt = store.create_batch("client-1", requests)
    orchestrator = FakeOrchestrator(store, fail=True)

    worker = DatabaseWorker(store, orchestrator)
    assert worker.run_once() is False

    job = store.get_job(receipt.job_ids[0], "client-1")
    batch = store.get_batch(receipt.batch_id, "client-1")
    assert job.status is JobStatus.FAILED
    assert job.error_summary == "Interrupted by worker failure; submit the source again to retry"
    assert batch.status == BatchStatus.FAILED.value
    assert worker.run_once() is False


def test_restart_recovery_keeps_completed_jobs_and_fails_only_unfinished_jobs(tmp_path):
    store = SqliteMetadataStore(tmp_path / "jobs.db")
    requests = [
        SourceRequest(SourceKind.SEARCH_QUERY, "Complete"),
        SourceRequest(SourceKind.SEARCH_QUERY, "Interrupted"),
    ]
    receipt = store.create_batch("client-1", requests)
    store.claim_next_batch()
    store.transition_job(receipt.job_ids[0], JobStatus.DOWNLOADING, stage="download")
    store.transition_job(receipt.job_ids[0], JobStatus.DOWNLOADED, stage="download")
    store.transition_job(receipt.job_ids[0], JobStatus.SEPARATING, stage="separation")
    store.transition_job(receipt.job_ids[0], JobStatus.COMPLETED, stage="completed")

    recovered = store.recover_interrupted_batches()

    assert recovered == 1
    assert store.get_job(receipt.job_ids[0], "client-1").status is JobStatus.COMPLETED
    assert store.get_job(receipt.job_ids[1], "client-1").status is JobStatus.FAILED
    assert store.get_batch(receipt.batch_id, "client-1").status == BatchStatus.PARTIAL.value
    assert store.claim_next_batch() is None


def test_worker_composition_uses_local_adapters_and_configured_database(tmp_path):
    database = tmp_path / "worker.db"
    settings = replace(
        Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"),
        database_url=f"sqlite:///{database.as_posix()}",
        storage_root=tmp_path / "media",
        temp_root=tmp_path / "temp",
    )

    worker = build_worker(settings)

    assert isinstance(worker, DatabaseWorker)
    assert isinstance(worker.orchestrator.downloader, PytubeDownloader)
    assert isinstance(worker.orchestrator.separator, DemucsSeparator)
    assert database.exists()


def test_worker_keeps_batch_download_phase_before_any_separation(tmp_path):
    orchestrator, store, _, events, _, _ = job_fixtures.build_orchestrator(tmp_path)
    requests = [
        SourceRequest(SourceKind.SEARCH_QUERY, "first"),
        SourceRequest(SourceKind.SEARCH_QUERY, "second"),
    ]
    orchestrator.submit_batch("client-1", requests)

    assert DatabaseWorker(store, orchestrator).run_once() is True

    kinds = [kind for kind, _ in events]
    assert max(i for i, kind in enumerate(kinds) if kind == "download") < min(
        i for i, kind in enumerate(kinds) if kind == "separate"
    )

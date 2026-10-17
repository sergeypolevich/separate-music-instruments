import ast
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from contracts import (
    AdapterError,
    BatchReceipt,
    DownloadedAudio,
    Downloader,
    FailureCategory,
    JobEvent,
    JobRepository,
    JobService,
    JobStatus,
    JobView,
    MediaAsset,
    MediaAssetType,
    MediaStorage,
    SeparationOptions,
    SeparationResult,
    SeparatorModel,
    StorageTarget,
    SourceAdapter,
    SourceKind,
    SourceRequest,
    StemArtifact,
    StemSeparator,
    VideoCandidate,
)


class FakeSourceAdapter:
    def normalize(self, value: str) -> SourceRequest:
        return SourceRequest(SourceKind.SEARCH_QUERY, value)


class FakeDownloader:
    def search(self, query: str) -> list[VideoCandidate]:
        return [VideoCandidate("video-1", query, "https://youtube.com/watch?v=video-1")]

    def download(self, source: VideoCandidate) -> DownloadedAudio:
        return DownloadedAudio(Path("audio.webm"), source, "webm", 1024)


class FakeSeparator:
    def available_models(self):
        return (SeparatorModel("htdemucs_6s", ("vocals",), {}),)

    def resolve_device(self, configured):
        return "cpu" if configured == "auto" else configured

    def validate_options(self, options):
        return None

    def separate(self, audio_path: Path, output_dir: Path, options: SeparationOptions) -> SeparationResult:
        return SeparationResult((StemArtifact("vocals", output_dir / "vocals.wav"),))


class FakeStorage:
    def audio_target(self, title, extension, source_id=None):
        return StorageTarget(f"{title}.{extension}", Path(title) / f"{title}.{extension}")

    def find_existing_audio(self, source):
        return None

    def validate_audio_file(self, path, media_format):
        return True

    def quarantine_audio(self, path):
        return None

    def stem_directory(self, title, source_id=None):
        return Path("Stems") / title

    def stem_file_target(self, title, stem_name, source_id=None):
        return StorageTarget(f"Stems/{title}/{stem_name}.wav", Path("Stems") / title / f"{stem_name}.wav")

    def temporary_directory(self, title, source_id=None):
        return Path("temp") / title

    def temporary_model_directory(self, title, model_name, source_id=None):
        return Path("temp") / title / model_name

    def stems_complete(self, title, expected_stems, source_id=None):
        return False

    def remove_temporary_directory(self, title, source_id=None):
        return None

    def save_audio(self, job_id, audio):
        return MediaAsset(uuid4(), job_id, MediaAssetType.SOURCE_AUDIO, "audio.webm", 1024)

    def save_stem(self, job_id, track_title, source_id, stem):
        return MediaAsset(uuid4(), job_id, MediaAssetType.STEM, f"{stem.name}.wav", 128, stem.name)

    def resolve_path(self, storage_key):
        return Path("managed/asset")

    def delete_asset(self, storage_key):
        return None


class FakeJobs:
    def submit_batch(self, owner_id, sources, idempotency_key=None):
        return BatchReceipt(uuid4(), (uuid4(),))

    def get_job(self, job_id, owner_id):
        return JobView(job_id, uuid4(), owner_id, JobStatus.QUEUED)


class FakeJobRepository:
    def create_batch(self, owner_id, sources, idempotency_key=None):
        return BatchReceipt(uuid4(), (uuid4(),))

    def claim_next_batch(self):
        return None

    def recover_interrupted_batches(self):
        return 0

    def list_jobs_for_batch(self, batch_id, owner_id):
        return []

    def list_batches(self, owner_id, limit=50):
        return []

    def update_project_source(self, job_id, source_id, title):
        return None

    def get_asset(self, asset_id, owner_id):
        return None

    def transition_job(self, job_id, status, *, stage=None, error_summary=None):
        return JobView(job_id, uuid4(), "client-1", status, stage, error_summary)

    def transition_batch(self, batch_id, status, result_summary=None):
        return None

    def record_retry(self, job_id, attempt):
        return None

    def add_asset(self, asset):
        return None

    def get_job(self, job_id, owner_id):
        return JobView(job_id, uuid4(), owner_id, JobStatus.QUEUED)

    def list_events(self, job_id, owner_id):
        return [JobEvent(uuid4(), job_id, "queued", datetime.now(timezone.utc))]


def test_fake_adapters_satisfy_public_protocols():
    assert isinstance(FakeSourceAdapter(), SourceAdapter)
    assert isinstance(FakeDownloader(), Downloader)
    assert isinstance(FakeSeparator(), StemSeparator)
    assert FakeSeparator().available_models()[0].name == "htdemucs_6s"
    assert isinstance(FakeStorage(), MediaStorage)
    assert isinstance(FakeJobs(), JobService)
    assert isinstance(FakeJobRepository(), JobRepository)


def test_contract_data_models_carry_typed_processing_and_asset_metadata(tmp_path):
    source = VideoCandidate("video-1", "Track", "https://youtube.com/watch?v=video-1")
    audio = DownloadedAudio(tmp_path / "source.webm", source, "webm", 1024)
    options = SeparationOptions("htdemucs_6s", "cpu", {"shifts": 1})
    stem = StemArtifact("vocals", tmp_path / "vocals.wav")
    asset = MediaAsset(uuid4(), uuid4(), MediaAssetType.STEM, "stems/track/vocals.wav", 128, "vocals")

    assert audio.source == source
    assert options.parameters["shifts"] == 1
    assert stem.name == asset.stem_name == "vocals"
    assert asset.asset_type is MediaAssetType.STEM


def test_adapter_failures_expose_category_and_retryability():
    transient = AdapterError("temporary service issue", FailureCategory.TRANSIENT)
    invalid = AdapterError("unsupported source", FailureCategory.INVALID_INPUT)

    assert transient.retryable is True
    assert invalid.retryable is False


def test_contract_module_does_not_import_provider_implementations():
    source = Path(__file__).parents[1] / "contracts.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert "pytubefix" not in imported
    assert "demucs" not in imported

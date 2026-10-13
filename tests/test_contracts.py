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
    def separate(self, audio_path: Path, output_dir: Path, options: SeparationOptions) -> SeparationResult:
        return SeparationResult((StemArtifact("vocals", output_dir / "vocals.wav"),))


class FakeStorage:
    def save_audio(self, job_id, audio):
        return MediaAsset(uuid4(), job_id, MediaAssetType.SOURCE_AUDIO, "audio.webm", 1024)

    def save_stem(self, job_id, stem):
        return MediaAsset(uuid4(), job_id, MediaAssetType.STEM, f"{stem.name}.wav", 128, stem.name)

    def resolve_path(self, asset_id):
        return Path("managed/asset")

    def delete_job_assets(self, job_id):
        return None


class FakeJobs:
    def submit_batch(self, owner_id, sources, idempotency_key=None):
        return BatchReceipt(uuid4(), (uuid4(),))

    def get_job(self, job_id, owner_id):
        return JobView(job_id, uuid4(), owner_id, JobStatus.QUEUED)


class FakeJobRepository:
    def create_batch(self, owner_id, sources, idempotency_key=None):
        return BatchReceipt(uuid4(), (uuid4(),))

    def transition_job(self, job_id, status, *, stage=None, error_summary=None):
        return JobView(job_id, uuid4(), "client-1", status, stage, error_summary)

    def get_job(self, job_id, owner_id):
        return JobView(job_id, uuid4(), owner_id, JobStatus.QUEUED)

    def list_events(self, job_id, owner_id):
        return [JobEvent(uuid4(), job_id, "queued", datetime.now(timezone.utc))]


def test_fake_adapters_satisfy_public_protocols():
    assert isinstance(FakeSourceAdapter(), SourceAdapter)
    assert isinstance(FakeDownloader(), Downloader)
    assert isinstance(FakeSeparator(), StemSeparator)
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

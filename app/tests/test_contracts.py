import ast
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from audio_stems.models import (
    AdapterError,
    DownloadedAudio,
    FailureCategory,
    MediaAsset,
    MediaAssetType,
    SeparationOptions,
    StemArtifact,
    VideoCandidate,
)


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
    source = Path(__file__).parents[1] / "audio_stems" / "models.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert "pytubefix" not in imported
    assert "demucs" not in imported

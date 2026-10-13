from pathlib import Path
from uuid import uuid4

import pytest

from contracts import AdapterError, DownloadedAudio, FailureCategory, MediaAssetType, MediaStorage, StemArtifact, VideoCandidate
from local_storage import LocalMediaStorage


def test_storage_preserves_audio_root_and_flat_stem_layout(tmp_path):
    storage = LocalMediaStorage(tmp_path / "library")

    audio = storage.audio_target("Song / Mix", "webm", "video-1")
    vocals = storage.stem_file_target("Song _ Mix", "vocals", "video-1")
    drums = storage.stem_file_target("Song _ Mix", "drums", "video-1")

    assert audio.storage_key == "Song _ Mix.webm"
    assert audio.path.parent == storage.root
    assert vocals.path.parent == drums.path.parent
    assert vocals.storage_key == "Stems/Song _ Mix/vocals.wav"
    assert not (vocals.path.parent / "vocals").exists()


@pytest.mark.parametrize(
    ("title", "expected"),
    [("CON", "_CON"), ("CON.wav", "_CON.wav"), ("Song: / Live?", "Song_ _ Live_"), ("...", "untitled")],
)
def test_storage_sanitizes_windows_names(tmp_path, title, expected):
    storage = LocalMediaStorage(tmp_path / "library")

    target = storage.audio_target(title, "webm", "video-1")

    assert target.path.name == f"{expected}.webm"
    assert target.path.parent == storage.root


def test_colliding_titles_get_stable_source_suffixes(tmp_path):
    root = tmp_path / "library"
    first = LocalMediaStorage(root)
    first_audio = first.audio_target("Same title", "webm", "video-1")
    first_stems = first.stem_directory("Same title", "video-1")
    second_audio = first.audio_target("Same title", "webm", "video-2")
    second_stems = first.stem_directory("Same title", "video-2")
    reopened = LocalMediaStorage(root)

    assert first_audio.path.name == "Same title.webm"
    assert second_audio.path.name == "Same title--video-2.webm"
    assert second_stems.name == "Same title--video-2"
    assert reopened.audio_target("Same title", "webm", "video-2") == second_audio
    assert reopened.stem_directory("Same title", "video-1") == first_stems


@pytest.mark.parametrize(
    "storage_key",
    ["../outside.wav", "Stems/../../outside.wav", "..\\outside.wav", "C:/outside.wav", "/outside.wav", "\\\\server\\share\\x", "file.txt:stream"],
)
def test_resolve_rejects_absolute_and_traversal_keys(tmp_path, storage_key):
    storage = LocalMediaStorage(tmp_path / "library")

    with pytest.raises(AdapterError) as error:
        storage.resolve_path(storage_key)

    assert error.value.category is FailureCategory.INVALID_INPUT


def test_storage_asset_operations_return_relative_managed_keys(tmp_path):
    storage = LocalMediaStorage(tmp_path / "library")
    assert isinstance(storage, MediaStorage)
    job_id = uuid4()
    source = VideoCandidate("video-7", "A track", "https://youtube.com/watch?v=video-7")
    audio_file = tmp_path / "download.webm"
    audio_file.write_bytes(b"\x1aE\xdf\xa3audio")
    audio = DownloadedAudio(audio_file, source, "webm", 5)

    audio_asset = storage.save_audio(job_id, audio)
    stem_file = tmp_path / "vocals.wav"
    stem_file.write_bytes(b"stem")
    stem_asset = storage.save_stem(job_id, "A track", "video-7", StemArtifact("vocals", stem_file))

    assert audio_asset.asset_type is MediaAssetType.SOURCE_AUDIO
    assert audio_asset.storage_key == "A track.webm"
    assert stem_asset.storage_key == "Stems/A track/vocals.wav"
    assert storage.resolve_path(audio_asset.storage_key).read_bytes() == b"\x1aE\xdf\xa3audio"
    assert storage.resolve_path(stem_asset.storage_key).read_bytes() == b"stem"


def test_existing_audio_is_reused_by_source_identity_after_storage_reopens(tmp_path):
    root = tmp_path / "library"
    storage = LocalMediaStorage(root)
    source = VideoCandidate("video-9", "Track title", "https://youtube.com/watch?v=video-9")
    target = storage.audio_target(source.title, "webm", source.source_id)
    target.path.write_bytes(b"\x1aE\xdf\xa3existing-audio")

    existing = LocalMediaStorage(root).find_existing_audio(source)

    assert existing is not None
    assert existing.path == target.path
    assert existing.byte_size == len(b"\x1aE\xdf\xa3existing-audio")
    assert existing.source.source_id == source.source_id

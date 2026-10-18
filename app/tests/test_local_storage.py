import errno
from uuid import uuid4

import numpy as np
import pytest
import soundfile

from audio_stems.models import AdapterError, DownloadedAudio, FailureCategory, MediaAssetType, StemArtifact, VideoCandidate
from audio_stems.storage import LocalMediaStorage


def test_storage_preserves_audio_root_and_flat_stem_layout(tmp_path):
    storage = LocalMediaStorage(tmp_path / "library")

    audio = storage.audio_target("Song / Mix", "webm", "video-1")
    vocals = storage.stem_file_target("Song _ Mix", "vocals", "video-1")
    drums = storage.stem_file_target("Song _ Mix", "drums", "video-1")

    assert audio.storage_key == "Song _ Mix.webm"
    assert audio.path.parent == storage.root
    assert vocals.path.parent == drums.path.parent
    assert vocals.storage_key == "Stems/Song _ Mix/vocals.flac"
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


def test_audio_lookup_miss_does_not_reserve_placeholder_video_id_as_title(tmp_path):
    storage = LocalMediaStorage(tmp_path / "library")
    placeholder = VideoCandidate("video-id", "video-id", "https://youtu.be/video-id")

    assert storage.find_existing_audio(placeholder) is None
    actual = storage.audio_target("Real Track Title", "webm", "video-id")

    assert actual.path.name == "Real Track Title.webm"


def _float_stem(path, level):
    soundfile.write(path, np.array([[level, -level], [level / 2, 0.0]] * 32, np.float32), 44100, subtype="FLOAT")
    return path


def test_stems_are_stored_as_16_bit_flac_scaled_by_one_shared_gain(tmp_path):
    storage = LocalMediaStorage(tmp_path / "library")
    loud = StemArtifact("vocals", _float_stem(tmp_path / "vocals.wav", 1.3))
    quiet = StemArtifact("bass", _float_stem(tmp_path / "bass.wav", 0.5))

    assets = storage.save_stems(uuid4(), "Song", "video-1", [loud, quiet])

    assert [asset.media_format for asset in assets] == ["flac", "flac"]
    assert [asset.storage_key for asset in assets] == ["Stems/Song/vocals.flac", "Stems/Song/bass.flac"]
    peaks = {}
    for asset in assets:
        info = soundfile.info(storage.resolve_path(asset.storage_key))
        assert (info.format, info.subtype, info.samplerate, info.channels) == ("FLAC", "PCM_16", 44100, 2)
        peaks[asset.stem_name] = abs(soundfile.read(storage.resolve_path(asset.storage_key))[0]).max()
    assert abs(peaks["vocals"] - 0.999) < 1e-3  # the loudest stem no longer clips
    assert abs(peaks["bass"] / peaks["vocals"] - 0.5 / 1.3) < 1e-3  # the balance is unchanged
    assert not loud.path.exists() and not quiet.path.exists()  # float temporaries are freed


def test_storing_undoes_the_separator_level_unless_that_would_clip(tmp_path):
    storage = LocalMediaStorage(tmp_path / "library")
    quiet = StemArtifact("vocals", _float_stem(tmp_path / "vocals.wav", 0.2))
    asset, = storage.save_stems(uuid4(), "Song", "video-1", [quiet], level=0.5)
    assert abs(abs(soundfile.read(storage.resolve_path(asset.storage_key))[0]).max() - 0.4) < 1e-3

    loud = StemArtifact("vocals", _float_stem(tmp_path / "loud.wav", 0.8))
    asset, = storage.save_stems(uuid4(), "Rock Song", "video-2", [loud], level=0.5)
    assert abs(abs(soundfile.read(storage.resolve_path(asset.storage_key))[0]).max() - 0.999) < 1e-3


def test_a_full_disk_while_storing_stems_is_reported_and_leaves_no_partial_file(monkeypatch, tmp_path):
    storage = LocalMediaStorage(tmp_path / "library")
    stem = StemArtifact("vocals", _float_stem(tmp_path / "vocals.wav", 0.5))
    storage.stem_file_target("Song", "vocals", "video-1")  # the title is already claimed in the index

    def no_space(*_args):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("audio_stems.storage.os.replace", no_space)
    with pytest.raises(AdapterError) as error:
        storage.save_stems(uuid4(), "Song", "video-1", [stem])

    assert error.value.category is FailureCategory.DISK_FULL
    assert list((storage.stems_root / "Song").iterdir()) == []


def test_complete_legacy_wav_stem_set_still_counts_as_existing(tmp_path):
    storage = LocalMediaStorage(tmp_path / "library")
    for name in ("vocals", "drums"):
        storage.stem_file_target("Old", name, "video-1", "wav").path.write_bytes(b"RIFF")

    found = storage.existing_stems("Old", ("vocals", "drums"), "video-1")

    assert {name: path.suffix for name, path in found.items()} == {"vocals": ".wav", "drums": ".wav"}
    assert storage.existing_stems("Old", ("vocals", "drums", "bass"), "video-1") is None


def test_stems_of_other_models_get_their_own_flat_folder_and_the_original_keeps_its_own(tmp_path):
    storage = LocalMediaStorage(tmp_path / "library")

    original = storage.stem_file_target("Song", "vocals", "video-1")
    other = storage.stem_file_target("Song", "vocals", "video-1", variant="htdemucs_ft")

    assert original.storage_key == "Stems/Song/vocals.flac"
    assert other.storage_key == "Stems/Song (htdemucs_ft)/vocals.flac"
    assert LocalMediaStorage(tmp_path / "library").stem_directory("Song", "video-1", "htdemucs_ft") == other.path.parent

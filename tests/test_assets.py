import asyncio
from dataclasses import replace
import struct
import time
import wave
from uuid import uuid4

import httpx

from api import create_app
from contracts import MediaAsset, MediaAssetType, SourceKind, SourceRequest
from demucs_adapter import DemucsSeparator
from local_storage import LocalMediaStorage
from metadata import SqliteMetadataStore
from settings import Settings
from sources import YouTubeSourceAdapter
from waveform import waveform_data


def build_asset_api(tmp_path):
    settings = replace(
        Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"),
        database_url=f"sqlite:///{(tmp_path / 'assets.db').as_posix()}",
        storage_root=tmp_path / "media",
    )
    repository = SqliteMetadataStore(tmp_path / "assets.db")
    storage = LocalMediaStorage(settings.storage_root)
    receipt = repository.create_batch("local", [SourceRequest(SourceKind.SEARCH_QUERY, "Asset test")])
    job_id = receipt.job_ids[0]
    source = storage.audio_target("Asset test", "wav", "video-1")
    source.path.write_bytes(b"0123456789abcdef")
    asset = MediaAsset(uuid4(), job_id, MediaAssetType.SOURCE_AUDIO, source.storage_key, 16, media_format="wav")
    repository.add_asset(asset)
    app = create_app(
        repository=repository,
        settings=settings,
        source_adapter=YouTubeSourceAdapter(settings.allowed_source_hosts),
        separator=DemucsSeparator(),
        storage=storage,
    )

    async def send(method, path, *, headers=None):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.request(method, path, headers=headers)

    return lambda method, path, headers=None: asyncio.run(send(method, path, headers=headers)), repository, storage, asset


def _write_mono_wav(path):
    # Four two-frame buckets with known, progressively larger absolute peaks.
    values = [1000, -2000, 4000, -6000, 8000, -10000, 12000, -16000]
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"".join(struct.pack("<h", value) for value in values))


def test_asset_stream_supports_closed_and_suffix_ranges(tmp_path):
    request, _, _, asset = build_asset_api(tmp_path)

    closed = request("GET", f"/api/v1/assets/{asset.asset_id}", {"Range": "bytes=3-8"})
    suffix = request("GET", f"/api/v1/assets/{asset.asset_id}", {"Range": "bytes=-4"})

    assert closed.status_code == 206
    assert closed.content == b"345678"
    assert closed.headers["content-range"] == "bytes 3-8/16"
    assert closed.headers["accept-ranges"] == "bytes"
    assert suffix.status_code == 206
    assert suffix.content == b"cdef"
    assert suffix.headers["content-range"] == "bytes 12-15/16"


def test_asset_stream_returns_full_content_and_missing_assets_are_stable_404(tmp_path):
    request, repository, storage, asset = build_asset_api(tmp_path)
    full = request("GET", f"/api/v1/assets/{asset.asset_id}")
    storage.resolve_path(asset.storage_key).unlink()
    missing_file = request("GET", f"/api/v1/assets/{asset.asset_id}")
    missing_id = request("GET", f"/api/v1/assets/{uuid4()}")

    assert full.status_code == 200 and full.content == b"0123456789abcdef"
    assert missing_file.status_code == 404
    assert missing_file.json()["error"]["code"] == "asset_unavailable"
    assert missing_id.status_code == 404
    assert missing_id.json()["error"]["code"] == "not_found"


def test_byte_range_outside_asset_size_returns_416_and_owner_scope_is_enforced(tmp_path):
    request, repository, _, asset = build_asset_api(tmp_path)
    outside = request("GET", f"/api/v1/assets/{asset.asset_id}", {"Range": "bytes=99-100"})
    foreign_batch = repository.create_batch("other-owner", [SourceRequest(SourceKind.SEARCH_QUERY, "Other")])
    foreign_asset = MediaAsset(
        uuid4(), foreign_batch.job_ids[0], MediaAssetType.SOURCE_AUDIO,
        asset.storage_key, asset.byte_size, media_format="wav",
    )
    repository.add_asset(foreign_asset)
    hidden = request("GET", f"/api/v1/assets/{foreign_asset.asset_id}")

    assert outside.status_code == 416
    assert outside.headers["content-range"] == "bytes */16"
    assert hidden.status_code == 404 and hidden.json()["error"]["code"] == "not_found"


def test_waveform_is_bounded_and_mapped_to_the_requested_asset(tmp_path):
    request, repository, storage, _source_asset = build_asset_api(tmp_path)
    wav_target = storage.stem_file_target("Asset test", "vocals", "video-1")
    _write_mono_wav(wav_target.path)
    receipt = repository.create_batch("local", [SourceRequest(SourceKind.SEARCH_QUERY, "Waveform")])
    job_id = receipt.job_ids[0]
    waveform_asset = MediaAsset(
        uuid4(), job_id, MediaAssetType.STEM, wav_target.storage_key, wav_target.path.stat().st_size,
        stem_name="vocals", media_format="wav",
    )
    repository.add_asset(waveform_asset)

    response = request("GET", f"/api/v1/assets/{waveform_asset.asset_id}/waveform?levels=4")
    body = response.json()

    assert response.status_code == 200
    assert body["asset_id"] == str(waveform_asset.asset_id)
    assert body["sample_rate"] == 8000
    assert body["channels"] == 1
    assert body["frame_count"] == 8
    assert len(body["levels"]) == 4
    assert body["levels"] == sorted(body["levels"])
    assert all(0 <= peak <= 1 for peak in body["levels"])


def test_waveform_rejects_non_wav_assets_and_excessive_level_counts(tmp_path):
    request, repository, storage, _ = build_asset_api(tmp_path)
    target = storage.audio_target("Compressed", "webm", "video-2")
    target.path.write_bytes(b"\x1aE\xdf\xa3audio")
    receipt = repository.create_batch("local", [SourceRequest(SourceKind.SEARCH_QUERY, "Compressed")])
    asset = MediaAsset(
        uuid4(), receipt.job_ids[0], MediaAssetType.SOURCE_AUDIO, target.storage_key,
        target.path.stat().st_size, media_format="webm",
    )
    repository.add_asset(asset)

    unsupported = request("GET", f"/api/v1/assets/{asset.asset_id}/waveform")
    too_many = request("GET", f"/api/v1/assets/{asset.asset_id}/waveform?levels=999999")

    assert unsupported.status_code == 415
    assert unsupported.json()["error"]["code"] == "unsupported_waveform_format"
    assert too_many.status_code == 422
    assert too_many.json()["error"]["code"] == "invalid_request"


def test_waveform_rejects_corrupt_wav_with_safe_error(tmp_path):
    request, repository, storage, _ = build_asset_api(tmp_path)
    target = storage.stem_file_target("Corrupt", "vocals", "video-3")
    target.path.write_bytes(b"not a wave file")
    receipt = repository.create_batch("local", [SourceRequest(SourceKind.SEARCH_QUERY, "Corrupt")])
    asset = MediaAsset(
        uuid4(), receipt.job_ids[0], MediaAssetType.STEM,
        target.storage_key, target.path.stat().st_size, stem_name="vocals", media_format="wav",
    )
    repository.add_asset(asset)

    response = request("GET", f"/api/v1/assets/{asset.asset_id}/waveform")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_waveform_source"
    assert "C:\\Users" not in response.text


def test_waveform_rejects_truncated_pcm_data_instead_of_fabricating_silence(tmp_path):
    request, repository, storage, _ = build_asset_api(tmp_path)
    target = storage.stem_file_target("Truncated", "vocals", "video-4")
    _write_mono_wav(target.path)
    target.path.write_bytes(target.path.read_bytes()[:-8])
    receipt = repository.create_batch("local", [SourceRequest(SourceKind.SEARCH_QUERY, "Truncated")])
    asset = MediaAsset(
        uuid4(), receipt.job_ids[0], MediaAssetType.STEM,
        target.storage_key, target.path.stat().st_size, stem_name="vocals", media_format="wav",
    )
    repository.add_asset(asset)

    response = request("GET", f"/api/v1/assets/{asset.asset_id}/waveform")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_waveform_source"


def test_waveform_reads_24_bit_pcm_peaks(tmp_path):
    path = tmp_path / "24bit.wav"
    values = [0x400000, -0x200000, -0x800000, 0x100000]  # 0.5, 0.25, 1.0, 0.125 of full scale
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(3)
        output.setframerate(8000)
        output.writeframes(b"".join(value.to_bytes(3, "little", signed=True) for value in values))

    assert waveform_data(path, 4)["levels"] == [0.5, 0.25, 1.0, 0.125]


def test_waveform_of_a_long_stereo_stem_is_fast(tmp_path):
    path = tmp_path / "long.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(44100)
        output.writeframes(bytes(60 * 44100 * 4))  # one minute of silence, ~10 MB

    started = time.perf_counter()
    data = waveform_data(path, 256)

    assert time.perf_counter() - started < 2  # the pure-Python scan took ~5 s here
    assert len(data["levels"]) == 256 and max(data["levels"]) == 0

"""Opt-in live end-to-end test for Band - Rock Song (Official Music Video).

The test uses the real pytubefix downloader and the real Demucs model. It is
skipped by default because it requires network access, a YouTube download, model
weights, CPU time, and permission to process the source audio.

Enable explicitly in PowerShell:
    $env:RUN_LIVE_E2E = "1"
    py -m pytest -m e2e -s
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import download_tracks as app


pytestmark = pytest.mark.e2e

QUERY = "Band - Rock Song (Official Music Video)"
EXPECTED_VIDEO_ID = "<video-id>"
EXPECTED_STEMS = ["vocals", "drums", "bass", "guitar", "piano", "other"]


def video_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.lower() in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/").split("/")[0] or None
    return parse_qs(parsed.query).get("v", [None])[0]


def test_rock_song_download_and_separate_end_to_end(monkeypatch, tmp_path):
    if os.getenv("RUN_LIVE_E2E") != "1":
        pytest.skip("Set RUN_LIVE_E2E=1 to run the live YouTube + Demucs test")

    # Keep all downloaded media and generated stems inside pytest's temporary dir.
    output_dir = tmp_path / "Music"
    stems_dir = output_dir / "Stems"
    temp_dir = output_dir / "_demucs_temp"
    output_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(app, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(app, "STEMS_DIR", stems_dir)
    monkeypatch.setattr(app, "TEMP_DIR", temp_dir)
    monkeypatch.setattr(app.time, "sleep", lambda *_args, **_kwargs: None)

    # Guard against accidentally downloading a different first search result.
    real_youtube = app.YouTube

    def require_official_video(url: str, *args, **kwargs):
        actual_id = video_id_from_url(url)
        assert actual_id == EXPECTED_VIDEO_ID, (
            f"Search selected video ID {actual_id!r}, expected the official video "
            f"{EXPECTED_VIDEO_ID}. Refusing to download a different result."
        )
        return real_youtube(url, *args, **kwargs)

    monkeypatch.setattr(app, "YouTube", require_official_video)

    audio_path = app.download_track(QUERY)
    assert audio_path is not None, "The official video could not be downloaded"
    assert audio_path.exists() and audio_path.stat().st_size > 0
    assert "blur" in audio_path.stem.lower()
    assert "rock song" in audio_path.stem.lower()

    app.separate_stems(audio_path)

    track_stems_dir = stems_dir / app.safe_filename(audio_path.stem)
    expected_paths = [track_stems_dir / f"{stem}.wav" for stem in EXPECTED_STEMS]

    missing = [path.name for path in expected_paths if not path.is_file() or path.stat().st_size == 0]
    assert not missing, f"Missing or empty stem files: {missing}"
    assert sorted(path.name for path in track_stems_dir.iterdir()) == sorted(
        f"{stem}.wav" for stem in EXPECTED_STEMS
    ), "All six stems should be stored flat in one per-track directory"
    assert not any(path.is_dir() for path in track_stems_dir.iterdir())

"""Offline regression tests for the current download_tracks.py script.

These tests deliberately mock YouTube and Demucs. They should run quickly and
must not access the network or execute an ML model.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import download_tracks as app


STEM_NAMES = ["vocals", "drums", "bass", "guitar", "piano", "other"]


class FakeStream:
    def __init__(self, subtype: str = "webm", abr: str = "160kbps", *, fail: bool = False):
        self.subtype = subtype
        self.abr = abr
        self.fail = fail
        self.download_calls = 0

    def download(self, output_path: str, filename: str) -> str:
        self.download_calls += 1
        if self.fail:
            raise OSError("simulated transient download failure")
        destination = Path(output_path) / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake-audio-content")
        return str(destination)


class FakeStreams:
    def __init__(self, preferred: FakeStream | None, fallback: FakeStream | None = None):
        self.preferred = preferred
        self.fallback = fallback
        self.filter_kwargs = None
        self.order_by_field = None
        self.desc_called = False
        self.first_called = False
        self.fallback_called = False

    def filter(self, **kwargs):
        self.filter_kwargs = kwargs
        return self

    def order_by(self, field: str):
        self.order_by_field = field
        return self

    def desc(self):
        self.desc_called = True
        return self

    def first(self):
        self.first_called = True
        return self.preferred

    def get_audio_only(self):
        self.fallback_called = True
        return self.fallback or self.preferred


class FakeYouTube:
    def __init__(self, title: str, streams: FakeStreams, url: str = "https://youtube.com/watch?v=fake123"):
        self.title = title
        self.streams = streams
        self.watch_url = url


def configure_download_mocks(monkeypatch, tmp_path: Path, *, title="Band - Rock Song", preferred=None, fallback=None, urls=None):
    output_dir = tmp_path / "Music"
    output_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(app, "STEMS_DIR", output_dir / "Stems")
    monkeypatch.setattr(app, "TEMP_DIR", output_dir / "_demucs_temp")

    streams = FakeStreams(preferred, fallback)
    video_urls = urls or ["https://youtube.com/watch?v=fake123"]
    search_calls = []
    youtube_calls = []

    def fake_search(query):
        search_calls.append(query)
        return SimpleNamespace(videos=[SimpleNamespace(watch_url=url) for url in video_urls])

    def fake_youtube(url, *args, **kwargs):
        youtube_calls.append(url)
        return FakeYouTube(title=title, streams=streams, url=url)

    monkeypatch.setattr(app, "Search", fake_search)
    monkeypatch.setattr(app, "YouTube", fake_youtube)
    monkeypatch.setattr(app.time, "sleep", lambda *_args, **_kwargs: None)
    return output_dir, streams, search_calls, youtube_calls


def test_safe_filename_removes_windows_invalid_characters():
    result = app.safe_filename('Song / 2: <Live>? "Mix" | Final')
    assert not any(char in result for char in '<>:"/\\|?*')
    assert result


def test_safe_filename_prefixes_windows_reserved_name():
    assert app.safe_filename("CON") == "_CON"


def test_download_prefers_highest_ordered_webm_audio_stream(monkeypatch, tmp_path):
    stream = FakeStream(subtype="webm", abr="160kbps")
    _, streams, search_calls, youtube_calls = configure_download_mocks(
        monkeypatch, tmp_path, preferred=stream
    )

    result = app.download_track("Band - Rock Song (Official Music Video)")

    assert result is not None and result.is_file()
    assert result.name == "Band - Rock Song.webm"
    assert search_calls == ["Band - Rock Song (Official Music Video)"]
    assert youtube_calls == ["https://youtube.com/watch?v=fake123"]
    assert streams.filter_kwargs == {"only_audio": True, "mime_type": "audio/webm"}
    assert streams.order_by_field == "abr"
    assert streams.desc_called
    assert stream.download_calls == 1


def test_download_falls_back_when_webm_is_unavailable(monkeypatch, tmp_path):
    fallback = FakeStream(subtype="m4a", abr="128kbps")
    _, streams, _, _ = configure_download_mocks(
        monkeypatch, tmp_path, preferred=None, fallback=fallback
    )

    result = app.download_track("Band - Rock Song")

    assert result is not None
    assert result.suffix == ".m4a"
    assert streams.fallback_called
    assert fallback.download_calls == 1


def test_existing_nonempty_audio_is_reused(monkeypatch, tmp_path):
    stream = FakeStream()
    output_dir, _, _, _ = configure_download_mocks(
        monkeypatch, tmp_path, title="Band - Rock Song", preferred=stream
    )
    existing = output_dir / "Band - Rock Song.webm"
    existing.write_bytes(b"existing-audio")

    result = app.download_track("Band - Rock Song")

    assert result == existing
    assert stream.download_calls == 0


def test_failed_download_is_retried_three_times_and_returns_none(monkeypatch, tmp_path):
    stream = FakeStream(fail=True)
    configure_download_mocks(monkeypatch, tmp_path, preferred=stream)

    result = app.download_track("Band - Rock Song")

    assert result is None
    assert stream.download_calls == 3


def test_download_uses_configured_retry_delays(monkeypatch, tmp_path):
    stream = FakeStream(fail=True)
    configure_download_mocks(monkeypatch, tmp_path, preferred=stream)
    delays = []
    monkeypatch.setattr(app.time, "sleep", delays.append)
    monkeypatch.setattr(app, "SETTINGS", replace(app.SETTINGS, retry_count=3, retry_delays=(0.25, 0.5)))

    assert app.download_track("Band - Rock Song") is None
    assert stream.download_calls == 3
    assert delays == [0.25, 0.5]


def test_batch_downloads_all_items_before_starting_any_separation(monkeypatch, tmp_path):
    tracks = ["Band - Rock Song", "A failed track", "Another track"]
    monkeypatch.setattr(app, "TRACKS", tracks)
    monkeypatch.setattr(app.time, "sleep", lambda *_args, **_kwargs: None)

    events = []
    downloaded = {
        tracks[0]: tmp_path / "Band - Rock Song.webm",
        tracks[2]: tmp_path / "Another track.webm",
    }
    for path in downloaded.values():
        path.write_bytes(b"fake-audio")

    def fake_download(query):
        events.append(("download", query))
        return downloaded.get(query)

    def fake_separate(path):
        events.append(("separate", Path(path).name))

    monkeypatch.setattr(app, "download_track", fake_download)
    monkeypatch.setattr(app, "separate_stems", fake_separate)

    app.main()

    download_positions = [i for i, event in enumerate(events) if event[0] == "download"]
    separation_positions = [i for i, event in enumerate(events) if event[0] == "separate"]

    assert len(download_positions) == len(tracks)
    assert len(separation_positions) == 2
    assert max(download_positions) < min(separation_positions)
    assert [events[i][1] for i in separation_positions] == [
        "Band - Rock Song.webm",
        "Another track.webm",
    ]


def test_separator_writes_six_stems_flat_and_cleans_temp_after_success(monkeypatch, tmp_path):
    output_dir = tmp_path / "Music"
    stems_dir = output_dir / "Stems"
    temp_dir = output_dir / "_demucs_temp"
    audio_path = output_dir / "Band - Rock Song (Official Music Video).webm"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake-audio")

    monkeypatch.setattr(app, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(app, "STEMS_DIR", stems_dir)
    monkeypatch.setattr(app, "TEMP_DIR", temp_dir)

    def fake_subprocess_run(command, check):
        assert check is True
        assert "htdemucs_6s" in command
        assert "-d" in command and command[command.index("-d") + 1] == "cpu"
        temp_output = Path(command[command.index("-o") + 1])
        source_path = Path(command[-1])
        result_dir = temp_output / "htdemucs_6s" / source_path.stem
        result_dir.mkdir(parents=True, exist_ok=True)
        for stem in STEM_NAMES:
            (result_dir / f"{stem}.wav").write_bytes(b"fake-wav-content")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(app.subprocess, "run", fake_subprocess_run)

    app.separate_stems(audio_path)

    track_stems_dir = stems_dir / app.safe_filename(audio_path.stem)
    assert sorted(path.name for path in track_stems_dir.iterdir()) == sorted(
        f"{stem}.wav" for stem in STEM_NAMES
    )
    assert all(path.is_file() and path.stat().st_size > 0 for path in track_stems_dir.iterdir())
    assert not any(path.is_dir() for path in track_stems_dir.iterdir())
    assert not (temp_dir / app.safe_filename(audio_path.stem)).exists()


def test_separator_skips_when_all_flat_stems_already_exist(monkeypatch, tmp_path):
    output_dir = tmp_path / "Music"
    stems_dir = output_dir / "Stems"
    temp_dir = output_dir / "_demucs_temp"
    audio_path = output_dir / "Band - Rock Song.webm"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake-audio")

    track_stems_dir = stems_dir / app.safe_filename(audio_path.stem)
    track_stems_dir.mkdir(parents=True)
    for stem in STEM_NAMES:
        (track_stems_dir / f"{stem}.wav").write_bytes(b"existing-stem")

    monkeypatch.setattr(app, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(app, "STEMS_DIR", stems_dir)
    monkeypatch.setattr(app, "TEMP_DIR", temp_dir)

    def should_not_run(*_args, **_kwargs):
        pytest.fail("Demucs should not run when all expected stems already exist")

    monkeypatch.setattr(app.subprocess, "run", should_not_run)
    app.separate_stems(audio_path)


def test_incomplete_stem_output_keeps_temp_and_can_be_repaired(monkeypatch, tmp_path):
    output_dir = tmp_path / "Music"
    stems_dir = output_dir / "Stems"
    temp_dir = output_dir / "_demucs_temp"
    audio_path = output_dir / "Band - Rock Song.webm"
    output_dir.mkdir(parents=True)
    audio_path.write_bytes(b"fake-audio")

    monkeypatch.setattr(app, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(app, "STEMS_DIR", stems_dir)
    monkeypatch.setattr(app, "TEMP_DIR", temp_dir)

    calls = 0

    def fake_subprocess_run(command, check):
        nonlocal calls
        calls += 1
        temp_output = Path(command[command.index("-o") + 1])
        source_path = Path(command[-1])
        result_dir = temp_output / "htdemucs_6s" / source_path.stem
        result_dir.mkdir(parents=True, exist_ok=True)
        expected = STEM_NAMES if calls == 2 else STEM_NAMES[:-1]
        for stem in expected:
            (result_dir / f"{stem}.wav").write_bytes(b"fake-wav-content")

    monkeypatch.setattr(app.subprocess, "run", fake_subprocess_run)
    track_stems_dir = stems_dir / app.safe_filename(audio_path.stem)
    temp_output = temp_dir / app.safe_filename(audio_path.stem)

    app.separate_stems(audio_path)

    assert sorted(path.name for path in track_stems_dir.iterdir()) == sorted(
        f"{stem}.wav" for stem in STEM_NAMES[:-1]
    )
    assert temp_output.exists()

    app.separate_stems(audio_path)

    assert calls == 2
    assert sorted(path.name for path in track_stems_dir.iterdir()) == sorted(
        f"{stem}.wav" for stem in STEM_NAMES
    )
    assert not temp_output.exists()


def test_separation_failure_does_not_stop_later_tracks(monkeypatch, tmp_path):
    tracks = ["First track", "Second track"]
    output_dir = tmp_path / "Music"
    stems_dir = output_dir / "Stems"
    temp_dir = output_dir / "_demucs_temp"
    output_dir.mkdir()
    audio_paths = [output_dir / f"{track}.webm" for track in tracks]
    for path in audio_paths:
        path.write_bytes(b"fake-audio")

    monkeypatch.setattr(app, "TRACKS", tracks)
    monkeypatch.setattr(app, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(app, "STEMS_DIR", stems_dir)
    monkeypatch.setattr(app, "TEMP_DIR", temp_dir)
    monkeypatch.setattr(app, "download_track", lambda query: audio_paths[tracks.index(query)])
    monkeypatch.setattr(app.time, "sleep", lambda *_args, **_kwargs: None)

    separation_calls = 0

    def fake_subprocess_run(command, check):
        nonlocal separation_calls
        separation_calls += 1
        if separation_calls == 1:
            raise subprocess.CalledProcessError(returncode=1, cmd=command)

        temp_output = Path(command[command.index("-o") + 1])
        source_path = Path(command[-1])
        result_dir = temp_output / "htdemucs_6s" / source_path.stem
        result_dir.mkdir(parents=True, exist_ok=True)
        for stem in STEM_NAMES:
            (result_dir / f"{stem}.wav").write_bytes(b"fake-wav-content")

    monkeypatch.setattr(app.subprocess, "run", fake_subprocess_run)

    app.main()

    second_stems = stems_dir / app.safe_filename(audio_paths[1].stem)
    assert separation_calls == 2
    assert sorted(path.name for path in second_stems.iterdir()) == sorted(
        f"{stem}.wav" for stem in STEM_NAMES
    )

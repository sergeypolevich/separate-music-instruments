import platform

import pytest
import soundfile

# Cached now: tests that fake subprocess.run (test_separator.py) must not get platform's own `ver` call, which
# came first only when another test had asked for the platform earlier in the same process.
platform.version()


def _header_length(path):
    try:
        return soundfile.info(str(path)).duration
    except Exception:  # what ffmpeg cannot read either: the length is unknown
        return None


@pytest.fixture(autouse=True)
def _song_length_without_ffmpeg(monkeypatch):
    """The pipeline asks ffmpeg for a song's length; starting it costs ~0.8 s on Windows (the virus scan), so the
    jobs' tests read the header instead. audio_duration itself is tested in test_separator.py."""
    monkeypatch.setattr("audio_stems.pipeline.audio_duration", _header_length)


@pytest.fixture(autouse=True)
def _analytics_in_tmp(monkeypatch, tmp_path):
    """Settings.load(environ={}) points analytics.db at the real ~/Audio Stems: a test's goes to its own tmp_path."""
    from pathlib import Path
    from audio_stems import analytics

    real, connect = Path.home() / "Audio Stems", analytics.connect
    monkeypatch.setattr(analytics, "connect",
                        lambda path: connect(tmp_path / "analytics.db" if Path(path).is_relative_to(real) else path))

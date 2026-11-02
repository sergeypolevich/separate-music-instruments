"""Live check of the best model on a machine without a system ffmpeg (opt-in, it runs the real model):

    $env:RUN_LIVE_E2E = "1"; py -m pytest -q -m e2e -s
"""
import os
from pathlib import Path
import shutil

import numpy as np
import pytest
import soundfile

from audio_stems.models import SeparationOptions
from audio_stems.separator import StemSeparator

pytestmark = pytest.mark.e2e


def test_the_best_model_separates_without_a_system_ffmpeg(monkeypatch, tmp_path):
    if os.getenv("RUN_LIVE_E2E") != "1":
        pytest.skip("Set RUN_LIVE_E2E=1 to run BS-RoFormer on a 10-second file")
    # A user's Mac had no ffmpeg: hide every one on PATH, as there (the app's own comes from imageio-ffmpeg).
    folders = [folder for folder in os.environ["PATH"].split(os.pathsep) if folder and not shutil.which("ffmpeg", path=folder)]
    monkeypatch.setenv("PATH", os.pathsep.join(folders))
    assert shutil.which("ffmpeg") is None
    rate = 44100
    t = np.arange(rate * 10) / rate
    tone = 0.3 * np.sin(2 * np.pi * 220 * t) + 0.2 * np.sign(np.sin(2 * np.pi * 2 * t)) * (t % 0.5 < 0.05)
    source = tmp_path / "tone.flac"
    soundfile.write(source, np.stack([tone, tone], axis=1), rate)

    result = StemSeparator().separate(source, tmp_path / "out", SeparationOptions("bs_roformer_sw", "cpu"))

    assert [stem.name for stem in result.stems] == ["vocals", "drums", "bass", "guitar", "piano", "other"]
    for stem in result.stems:
        with soundfile.SoundFile(stem.path) as audio:
            assert audio.subtype == "FLOAT" and abs(audio.frames / audio.samplerate - 10) < 0.1, stem.path.name
    assert Path(result.stems[0].path).parent == tmp_path / "out" / "bs_roformer_sw"

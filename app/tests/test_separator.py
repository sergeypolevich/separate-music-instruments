import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import time
import wave

import imageio_ffmpeg
import numpy as np
import pytest
import soundfile

from audio_stems.models import AdapterError, FailureCategory, SeparationOptions
from audio_stems.separator import StemSeparator, audio_duration, decode_to_wav, detect_compute_devices, run_with_progress


def test_separator_reports_model_names_and_supported_options():
    separator = StemSeparator()

    models = separator.available_models()

    stems = {model.name: model.stems for model in models}
    assert set(stems) == {"htdemucs_6s", "bs_roformer_sw"}  # the user kept the fast model and the best one
    assert stems["htdemucs_6s"] == stems["bs_roformer_sw"] == ("vocals", "drums", "bass", "guitar", "piano", "other")
    assert all(model.label and model.description for model in models)
    speed = {model.name: model.seconds_per_audio_second for model in models}
    assert speed == {"htdemucs_6s": 0.9, "bs_roformer_sw": 15.6}  # measured on the test laptop
    assert separator.model_stems("bs_roformer_sw") == stems["bs_roformer_sw"]
    with pytest.raises(AdapterError):
        separator.model_stems("made-up-model")


def test_device_detection_lists_only_available_torch_backends_in_priority_order():
    class Torch:
        class cuda:
            @staticmethod
            def is_available():
                return True

        class backends:
            class mps:
                @staticmethod
                def is_available():
                    return True

    assert detect_compute_devices(Torch) == ("cuda", "mps", "cpu")


def test_device_detection_handles_cpu_only_and_backend_errors():
    class CpuOnlyTorch:
        class cuda:
            @staticmethod
            def is_available():
                return False

        class backends:
            class mps:
                @staticmethod
                def is_available():
                    return False

    class BrokenTorch:
        class cuda:
            @staticmethod
            def is_available():
                raise RuntimeError("backend probe failed")

    cpu = ("cpu",)
    assert detect_compute_devices(CpuOnlyTorch) == cpu
    assert detect_compute_devices(BrokenTorch) == cpu


def test_device_detection_falls_back_to_cpu_when_torch_is_missing(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def without_torch(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("torch is missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_torch)
    assert detect_compute_devices() == ("cpu",)


def test_separator_passes_selected_model_and_device(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, *, check, **_kwargs):
        calls.append((command, check))
        if "-m" not in command:  # the bundled ffmpeg decoding the source first
            _decode_only(command)
            return
        result = tmp_path / "output" / "htdemucs_6s" / "source"
        result.mkdir(parents=True)
        for name in ("vocals", "drums", "bass", "guitar", "piano", "other"):
            (result / f"{name}.wav").write_bytes(b"wav")

    monkeypatch.setattr("audio_stems.separator.subprocess.run", fake_run)
    monkeypatch.setattr("audio_stems.separator.run_with_progress", lambda command, _passes, _progress: fake_run(command, check=True))
    source = tmp_path / "source.wav"
    source.write_bytes(b"RIFF0000WAVE")
    options = SeparationOptions("htdemucs_6s", "cpu")

    result = StemSeparator().separate(source, tmp_path / "output", options)

    decode, _ = calls[0]
    assert decode[0] == imageio_ffmpeg.get_ffmpeg_exe() and decode[decode.index("-i") + 1] == str(source)
    command, checked = calls[1]
    assert checked is True
    assert command[-1] == str(tmp_path / "output" / "source.wav")  # Demucs gets the decoded, scaled copy
    assert result.level == 2.0  # the fake decode peaks at 0.25, scaled to 0.5
    assert command[command.index("-n") + 1] == "htdemucs_6s"
    assert command[command.index("-d") + 1] == "cpu"
    assert "--shifts" not in command and "--overlap" not in command
    # Float output with no per-stem clipping control; storage applies one shared gain later.
    assert "--float32" in command and command[command.index("--clip-mode") + 1] == "none"
    assert not Path(command[-1]).exists() and not Path(decode[-1]).exists()  # decoded copies are removed
    assert len(result.stems) == 6


@pytest.mark.parametrize("options", [SeparationOptions("made-up-model", "cpu")])
def test_separator_rejects_unsupported_model(options, tmp_path):
    with pytest.raises(AdapterError) as error:
        StemSeparator().separate(tmp_path / "source.wav", tmp_path / "out", options)

    assert error.value.category is FailureCategory.INVALID_INPUT


def test_separator_rejects_hardware_backend_that_is_not_available(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "audio_stems.separator.detect_compute_devices",
        lambda: ("cpu",),
    )
    with pytest.raises(AdapterError) as error:
        StemSeparator().validate_options(SeparationOptions("htdemucs_6s", "mps"))
    assert error.value.category is FailureCategory.INVALID_INPUT


def _decode_only(command, **_kwargs):
    if "-m" not in command:  # ffmpeg: leave a decoded float file; Demucs: produce nothing
        soundfile.write(command[-1], np.full((32, 2), 0.25, np.float32), 48000, subtype="FLOAT")


def test_separator_reports_missing_demucs_outputs_as_permanent(monkeypatch, tmp_path):
    monkeypatch.setattr("audio_stems.separator.subprocess.run", _decode_only)
    monkeypatch.setattr("audio_stems.separator.run_with_progress", lambda command, _passes, _progress: _decode_only(command, check=True))
    source = tmp_path / "source.wav"
    source.touch()

    with pytest.raises(AdapterError) as error:
        StemSeparator().separate(source, tmp_path / "out", SeparationOptions("htdemucs_6s", "cpu"))

    assert error.value.category is FailureCategory.PERMANENT


def test_auto_device_resolves_to_the_best_available_backend(monkeypatch):
    monkeypatch.setattr("audio_stems.separator.detect_compute_devices", lambda: ("mps", "cpu"))

    assert StemSeparator().resolve_device("auto") == "mps"
    assert StemSeparator().resolve_device("cpu") == "cpu"


def test_bundled_ffmpeg_decodes_audio_to_float_wav(tmp_path):
    source = tmp_path / "tone.wav"
    with wave.open(str(source), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(48000)
        out.writeframes(b"".join(struct.pack("<hh", v, v) for v in (int(30000 * math.sin(i / 10)) for i in range(4800))))

    target = tmp_path / "decoded.wav"
    level = decode_to_wav(source, target)

    samples, rate = soundfile.read(target, dtype="float32")
    assert soundfile.info(target).subtype == "FLOAT" and rate == 48000 and samples.shape == (4800, 2)
    assert abs(np.abs(samples).max() - 0.5) < 1e-6  # scaled to a 0.5 peak for headroom
    assert abs(level * np.abs(samples).max() / 0.5 - level) < 1e-6 and 0.54 < level < 0.56
    assert sorted(path.name for path in tmp_path.iterdir()) == ["decoded.wav", "tone.wav"]


def test_undecodable_source_is_a_permanent_separation_failure(tmp_path):
    broken = tmp_path / "broken.webm"
    broken.write_bytes(b"not audio")
    with pytest.raises(AdapterError) as error:
        decode_to_wav(broken, tmp_path / "out.wav")
    assert error.value.category is FailureCategory.PERMANENT


def test_separation_stops_before_demucs_when_the_disk_is_too_full(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        _decode_only(command, **kwargs)

    monkeypatch.setattr("audio_stems.separator.subprocess.run", fake_run)
    monkeypatch.setattr("audio_stems.separator.run_with_progress", lambda command, _passes, _progress: fake_run(command, check=True))
    monkeypatch.setattr("audio_stems.separator.shutil.disk_usage", lambda _path: type("Usage", (), {"free": 10})())
    source = tmp_path / "source.webm"
    source.write_bytes(b"audio")

    with pytest.raises(AdapterError) as error:
        StemSeparator().separate(source, tmp_path / "out", SeparationOptions("htdemucs_6s", "cpu"))

    assert error.value.category is FailureCategory.DISK_FULL
    assert "Недостаточно места на диске" in str(error.value) and "свободно 0 МБ" in str(error.value)
    assert len(calls) == 1  # only the decode ran
    assert not (tmp_path / "out" / "source.wav").exists()


_TQDM_SCRIPT = r"""
import sys
for total in (10.0, 10.0):  # two model passes, each redrawing its bar with \r like tqdm
    for done in (0.0, 2.5, 5.0, 10.0):
        sys.stderr.write(f"\r {int(done * 10)}%|##   | {done}/{total} [00:01<00:02, 1.0seconds/s]")
        sys.stderr.flush()
    sys.stderr.write("\n")
sys.exit(int(sys.argv[1]))
"""


def test_tqdm_bars_of_every_model_pass_become_one_rising_progress():
    seen = []
    run_with_progress([sys.executable, "-c", _TQDM_SCRIPT, "0"], 2, seen.append)

    assert seen == [0.0, 0.125, 0.25, 0.5, 0.625, 0.75, 1.0]


def test_a_failing_separator_run_raises_with_the_end_of_its_output():
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_with_progress([sys.executable, "-c", _TQDM_SCRIPT, "3"], 1, None)
    assert error.value.returncode == 3 and b"10.0/10.0" in error.value.stderr


def test_roformer_runs_audio_separator_in_its_own_process_and_maps_its_file_names(monkeypatch, tmp_path):
    runs = []

    def fake_run(command, passes, progress):
        runs.append((command, passes))
        arguments = json.loads(command[-1])
        out = Path(arguments["out"])
        out.mkdir(parents=True)
        for stem in ("Vocals", "Drums", "Bass", "Guitar", "Piano", "Other"):
            (out / f"source_({stem})_BS-Roformer-SW.wav").write_bytes(b"RIFF")

    monkeypatch.setattr("audio_stems.separator.subprocess.run", _decode_only)
    monkeypatch.setattr("audio_stems.separator.run_with_progress", fake_run)
    source = tmp_path / "source.webm"
    source.write_bytes(b"audio")

    result = StemSeparator(tmp_path / "models").separate(source, tmp_path / "out", SeparationOptions("bs_roformer_sw", "cpu"))

    command, passes = runs[0]
    arguments = json.loads(command[-1])
    assert command[:2] == [sys.executable, "-c"] and "audio_separator.separator" in command[2]
    assert arguments["checkpoint"] == "BS-Roformer-SW.ckpt" and arguments["models"] == str(tmp_path / "models")
    assert arguments["overlap"] == 2  # the quality the user chose after listening
    assert arguments["input"] == str(tmp_path / "out" / "source.wav") and passes == 1
    assert [stem.name for stem in result.stems] == ["vocals", "drums", "bass", "guitar", "piano", "other"]
    assert result.stems[0].path.name == "source_(Vocals)_BS-Roformer-SW.wav"


def test_progress_arrives_while_the_separator_runs_not_at_its_exit():
    # Like tqdm in a pipe: carriage-return redraws, no explicit flush, then a pause before exit.
    script = "\n".join([
        "import sys, time",
        "sys.stderr.write('\\r 1/2 [00:01<00:01]')",
        "time.sleep(2)",
        "sys.stderr.write('\\r 2/2 [00:02<00:00]\\n')",
    ])
    started = time.perf_counter()
    seen = []
    run_with_progress([sys.executable, "-c", script], 1, lambda value: seen.append((value, time.perf_counter() - started)))
    finished = time.perf_counter() - started

    assert [value for value, _ in seen] == [0.5, 1.0]
    assert seen[0][1] < finished - 1.5  # the half-way value was not held back until exit


def test_the_song_length_is_read_from_the_header(tmp_path):
    path = tmp_path / "five.wav"
    soundfile.write(path, np.zeros((5 * 8000, 2), np.float32), 8000)
    broken = tmp_path / "broken.webm"
    broken.write_bytes(b"not audio")

    assert abs(audio_duration(path) - 5.0) < 0.05
    assert audio_duration(broken) is None

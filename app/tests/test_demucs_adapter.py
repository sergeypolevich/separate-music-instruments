import math
from pathlib import Path
import struct
import wave

import imageio_ffmpeg
import numpy as np
import pytest

from audio_stems.models import AdapterError, FailureCategory, SeparationOptions
from audio_stems.separator import DemucsSeparator, decode_to_wav, detect_compute_devices


def test_separator_reports_model_names_and_supported_options():
    separator = DemucsSeparator()

    models = separator.available_models()

    assert {model.name for model in models} == {"htdemucs_6s"}
    assert "htdemucs_6s" in {model.name for model in models}
    six_stem = next(model for model in models if model.name == "htdemucs_6s")
    assert six_stem.stems == ("vocals", "drums", "bass", "guitar", "piano", "other")


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
            Path(command[-1]).write_bytes(b"RIFF0000WAVE")
            return
        result = tmp_path / "output" / "htdemucs_6s" / "source"
        result.mkdir(parents=True)
        for name in ("vocals", "drums", "bass", "guitar", "piano", "other"):
            (result / f"{name}.wav").write_bytes(b"wav")

    monkeypatch.setattr("audio_stems.separator.subprocess.run", fake_run)
    source = tmp_path / "source.wav"
    source.write_bytes(b"RIFF0000WAVE")
    options = SeparationOptions("htdemucs_6s", "cpu")

    result = DemucsSeparator().separate(source, tmp_path / "output", options)

    decode, _ = calls[0]
    assert decode[0] == imageio_ffmpeg.get_ffmpeg_exe() and decode[decode.index("-i") + 1] == str(source)
    command, checked = calls[1]
    assert checked is True
    assert command[-1] == decode[-1]  # Demucs separates the decoded float WAV, not the original file
    assert command[command.index("-n") + 1] == "htdemucs_6s"
    assert command[command.index("-d") + 1] == "cpu"
    assert "--shifts" not in command and "--overlap" not in command
    assert len(result.stems) == 6


@pytest.mark.parametrize("options", [SeparationOptions("made-up-model", "cpu")])
def test_separator_rejects_unsupported_model(options, tmp_path):
    with pytest.raises(AdapterError) as error:
        DemucsSeparator().separate(tmp_path / "source.wav", tmp_path / "out", options)

    assert error.value.category is FailureCategory.INVALID_INPUT


def test_separator_rejects_hardware_backend_that_is_not_available(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "audio_stems.separator.detect_compute_devices",
        lambda: ("cpu",),
    )
    with pytest.raises(AdapterError) as error:
        DemucsSeparator().validate_options(SeparationOptions("htdemucs_6s", "mps"))
    assert error.value.category is FailureCategory.INVALID_INPUT


def test_separator_reports_missing_demucs_outputs_as_permanent(monkeypatch, tmp_path):
    monkeypatch.setattr("audio_stems.separator.subprocess.run", lambda *args, **kwargs: None)
    source = tmp_path / "source.wav"
    source.touch()

    with pytest.raises(AdapterError) as error:
        DemucsSeparator().separate(source, tmp_path / "out", SeparationOptions("htdemucs_6s", "cpu"))

    assert error.value.category is FailureCategory.PERMANENT


def test_auto_device_resolves_to_the_best_available_backend(monkeypatch):
    monkeypatch.setattr("audio_stems.separator.detect_compute_devices", lambda: ("mps", "cpu"))

    assert DemucsSeparator().resolve_device("auto") == "mps"
    assert DemucsSeparator().resolve_device("cpu") == "cpu"


def test_bundled_ffmpeg_decodes_audio_to_float_wav(tmp_path):
    source = tmp_path / "tone.wav"
    with wave.open(str(source), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(48000)
        out.writeframes(b"".join(struct.pack("<hh", v, v) for v in (int(30000 * math.sin(i / 10)) for i in range(4800))))

    target = tmp_path / "decoded.wav"
    decode_to_wav(source, target)

    data = target.read_bytes()
    assert data[:4] == b"RIFF" and struct.unpack("<H", data[20:22])[0] == 3  # WAVE_FORMAT_IEEE_FLOAT
    samples = np.frombuffer(data[data.index(b"data") + 8:], np.float32)
    assert samples.size == 9600 and 0.9 < np.abs(samples).max() < 0.92


def test_undecodable_source_is_a_permanent_separation_failure(tmp_path):
    broken = tmp_path / "broken.webm"
    broken.write_bytes(b"not audio")
    with pytest.raises(AdapterError) as error:
        decode_to_wav(broken, tmp_path / "out.wav")
    assert error.value.category is FailureCategory.PERMANENT

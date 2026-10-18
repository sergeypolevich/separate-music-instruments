"""Demucs command-line adapter; no Demucs imports escape this boundary."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import imageio_ffmpeg

from audio_stems.models import (
    AdapterError,
    FailureCategory,
    SeparationOptions,
    SeparationResult,
    SeparatorModel,
    StemArtifact,
)


_STEMS = ("vocals", "drums", "bass", "guitar", "piano", "other")
_MODELS = ("htdemucs_6s",)


def detect_compute_devices(torch_module=None) -> tuple[str, ...]:
    """Return devices usable by the installed PyTorch build in preference order: CUDA, MPS, CPU."""
    if torch_module is None:
        try:
            import torch as torch_module
        except Exception:
            return ("cpu",)
    try:
        devices = []
        if torch_module.cuda.is_available():
            devices.append("cuda")
        mps_backend = getattr(getattr(torch_module, "backends", None), "mps", None)
        if mps_backend is not None and mps_backend.is_available():
            devices.append("mps")
        return (*devices, "cpu")
    except Exception:
        return ("cpu",)


def decode_to_wav(source: Path, target: Path) -> None:
    """Decode the source to float WAV with the ffmpeg bundled by imageio-ffmpeg, so separation needs
    no system ffmpeg; float keeps decoded peaks above 0 dBFS intact."""
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-i", str(source), "-vn", "-c:a", "pcm_f32le", str(target)]
    try:
        subprocess.run(command, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AdapterError("Audio could not be decoded for separation", FailureCategory.PERMANENT) from exc


class DemucsSeparator:
    """Run Demucs and return the six supported WAV stems."""

    def available_models(self) -> tuple[SeparatorModel, ...]:
        # These are stable Demucs model identifiers supported by its CLI.
        # The capability endpoint is intentionally independent of loading weights.
        return tuple(SeparatorModel(name, _STEMS) for name in _MODELS)

    def resolve_device(self, configured: str) -> str:
        return detect_compute_devices()[0] if configured == "auto" else configured

    def separate(
        self,
        audio_path: Path,
        output_dir: Path,
        options: SeparationOptions,
    ) -> SeparationResult:
        self.validate_options(options)
        output_dir.mkdir(parents=True, exist_ok=True)
        decoded = output_dir / f"{audio_path.stem}.wav"
        decode_to_wav(audio_path, decoded)
        command = [
            sys.executable, "-m", "demucs", "-n", options.model_name,
            "-d", options.device, "-o", str(output_dir),
        ]
        command.append(str(decoded))
        try:
            subprocess.run(command, check=True)
        except FileNotFoundError as exc:
            raise AdapterError("Demucs executable is unavailable", FailureCategory.PERMANENT) from exc
        except subprocess.CalledProcessError as exc:
            raise AdapterError(f"Demucs exited with status {exc.returncode}", FailureCategory.PERMANENT) from exc
        except OSError as exc:
            raise AdapterError("Demucs could not be started", FailureCategory.TRANSIENT) from exc

        model_output = output_dir / options.model_name
        candidates = sorted((path for path in model_output.iterdir() if path.is_dir()), key=lambda p: p.name) if model_output.is_dir() else []
        if not candidates:
            raise AdapterError("Demucs output directory was not created", FailureCategory.PERMANENT)
        track_dir = next((path for path in candidates if path.name == audio_path.stem), candidates[0])
        artifacts = tuple(
            StemArtifact(name, track_dir / f"{name}.wav")
            for name in _STEMS
            if (track_dir / f"{name}.wav").is_file()
        )
        if len(artifacts) != len(_STEMS):
            raise AdapterError("Demucs did not produce all six expected stems", FailureCategory.PERMANENT)
        return SeparationResult(artifacts)

    def validate_options(self, options: SeparationOptions) -> None:
        if options.model_name not in _MODELS:
            raise AdapterError("Unsupported Demucs model", FailureCategory.INVALID_INPUT)
        if options.device not in detect_compute_devices():
            raise AdapterError("Unsupported Demucs device", FailureCategory.INVALID_INPUT)

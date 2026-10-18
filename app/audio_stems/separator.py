"""Demucs command-line adapter; no Demucs imports escape this boundary."""
from __future__ import annotations

import logging
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Callable

import imageio_ffmpeg
import soundfile

from audio_stems.models import (
    AdapterError,
    FailureCategory,
    SeparationOptions,
    SeparationResult,
    SeparatorModel,
    StemArtifact,
    disk_full_error,
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


logger = logging.getLogger("audio_stems.separator")
# tqdm prints "45%|####  | 52.6/117.0 [00:30<00:37, ...]" to stderr, redrawn with \r.
_TQDM = re.compile(rb"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?) \[")
# tqdm bars per run: a bag of models (htdemucs_ft) draws one bar per model.
_PASSES = {"htdemucs_6s": 1}
_INPUT_PEAK = 0.5
_BLOCK_FRAMES = 1 << 20


def run_with_progress(command: list[str], passes: int, progress: Callable[[float], None] | None) -> None:
    """Run a separator CLI and turn its tqdm bars, one per model pass, into 0..1 progress."""
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    done, last, pending, tail = 0, 0.0, b"", b""
    assert process.stderr is not None
    while chunk := process.stderr.read1(4096):
        tail = (tail + chunk)[-4096:]
        *lines, pending = re.split(rb"[\r\n]", pending + chunk)
        for line in lines:
            match = _TQDM.search(line)
            if not match or float(match[2]) <= 0:
                continue
            fraction = min(1.0, float(match[1]) / float(match[2]))
            if fraction < last - 0.5:  # a new bar: the next model pass started
                done += 1
            last = fraction
            if progress is not None:
                progress(min(1.0, (done + fraction) / passes))
    if process.wait():
        logger.error("Separator failed: %s", tail.decode("utf-8", "replace")[-1500:], extra={"event": "separation_failed"})
        raise subprocess.CalledProcessError(process.returncode, command, stderr=tail)


def decode_to_wav(source: Path, target: Path) -> float:
    """Decode the source to float WAV with the ffmpeg bundled by imageio-ffmpeg (no system ffmpeg
    needed) and scale it to a 0.5 peak. Demucs is scale-invariant but clamps its float output to
    +-1, so the headroom keeps loud stems from clipping. Returns the applied level."""
    decoded = target.with_name(target.stem + ".decoded.wav")
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-i", str(source), "-vn", "-c:a", "pcm_f32le", str(decoded)]
    try:
        subprocess.run(command, check=True, capture_output=True)
        with soundfile.SoundFile(decoded) as audio:
            peak = max((float(abs(block).max()) for block in audio.blocks(_BLOCK_FRAMES, dtype="float32")), default=0.0)
            level = _INPUT_PEAK / peak if peak > 0 else 1.0
            audio.seek(0)
            with soundfile.SoundFile(target, "w", audio.samplerate, audio.channels, "FLOAT") as output:
                for block in audio.blocks(_BLOCK_FRAMES, dtype="float32"):
                    output.write(block * level)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:  # libsndfile raises RuntimeError
        raise AdapterError("Audio could not be decoded for separation", FailureCategory.PERMANENT) from exc
    finally:
        decoded.unlink(missing_ok=True)
    return level


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
        progress: Callable[[float], None] | None = None,
    ) -> SeparationResult:
        self.validate_options(options)
        output_dir.mkdir(parents=True, exist_ok=True)
        decoded = output_dir / f"{audio_path.stem}.wav"
        level = decode_to_wav(audio_path, decoded)
        # Float stems at 44.1 kHz take about as much space each as the 48 kHz float decode,
        # and the FLAC copies are written while the float files still exist.
        needed = decoded.stat().st_size * (len(_STEMS) + 1)
        free = shutil.disk_usage(output_dir).free
        if free < needed:
            decoded.unlink(missing_ok=True)
            raise disk_full_error(needed, free)
        # Float output without per-stem rescaling, which would shift the balance; storage scales
        # all stems by one gain instead.
        command = [
            sys.executable, "-m", "demucs", "-n", options.model_name,
            "-d", options.device, "-o", str(output_dir), "--float32", "--clip-mode", "none",
        ]
        command.append(str(decoded))
        try:
            run_with_progress(command, _PASSES.get(options.model_name, 1), progress)
        except subprocess.CalledProcessError as exc:
            if shutil.disk_usage(output_dir).free < needed // 2:
                raise disk_full_error(needed, shutil.disk_usage(output_dir).free) from exc
            raise AdapterError(f"Demucs exited with status {exc.returncode}", FailureCategory.PERMANENT) from exc
        except FileNotFoundError as exc:
            raise AdapterError("Demucs executable is unavailable", FailureCategory.PERMANENT) from exc
        except OSError as exc:
            raise AdapterError("Demucs could not be started", FailureCategory.TRANSIENT) from exc
        finally:
            decoded.unlink(missing_ok=True)

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
        return SeparationResult(artifacts, level)

    def validate_options(self, options: SeparationOptions) -> None:
        if options.model_name not in _MODELS:
            raise AdapterError("Unsupported Demucs model", FailureCategory.INVALID_INPUT)
        if options.device not in detect_compute_devices():
            raise AdapterError("Unsupported Demucs device", FailureCategory.INVALID_INPUT)

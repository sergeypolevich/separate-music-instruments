"""Stem separation through the Demucs and audio-separator command lines; no ML imports escape this boundary."""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
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


_SIX = ("vocals", "drums", "bass", "guitar", "piano", "other")
_FOUR = ("vocals", "drums", "bass", "other")


@dataclass(frozen=True)
class _Model:
    label: str
    description: str
    stems: tuple[str, ...]
    engine: str  # "demucs" or "audio-separator"
    passes: int = 1  # tqdm bars per run: a bag of models draws one per model
    checkpoint: str | None = None
    overlap: int | None = None


# Chosen by SDR/SAR on MUSDB18 previews and CPU time on a 4-core laptop (DEVELOPMENT_STATUS, stage 28).
_MODELS = {
    "htdemucs_6s": _Model("Demucs, быстрая", "Самая быстрая: около 4 минут на песню длиной 4 минуты.", _SIX, "demucs"),
    "htdemucs_ft": _Model(
        "Demucs fine-tuned",
        "Самый чистый бас, «прочее» чище быстрой модели, барабаны чуть хуже; около 21 минуты.", _FOUR, "demucs", passes=4,
    ),
    "bs_roformer_sw": _Model(
        "BS-RoFormer SW",
        "Лучшие вокал, барабаны и «прочее», меньше всего артефактов; около 28 минут.",
        _SIX, "audio-separator", checkpoint="BS-Roformer-SW.ckpt", overlap=1,
    ),
}
# Runs audio-separator in its own process so its memory is freed after every song.
_AUDIO_SEPARATOR = (
    "import json, sys\n"
    "from audio_separator.separator import Separator\n"
    "a = json.loads(sys.argv[1])\n"
    "s = Separator(output_dir=a['out'], model_file_dir=a['models'], output_format='WAV', normalization_threshold=1.0,\n"
    "              mdxc_params={'segment_size': 256, 'override_model_segment_size': False, 'batch_size': 1,\n"
    "                           'overlap': a['overlap'], 'pitch_shift': 0})\n"
    "s.load_model(model_filename=a['checkpoint'])\n"
    "s.separate(a['input'])\n"
)


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
_INPUT_PEAK = 0.5
_BLOCK_FRAMES = 1 << 20


def run_with_progress(command: list[str], passes: int, progress: Callable[[float], None] | None) -> None:
    """Run a separator CLI and turn its tqdm bars, one per model pass, into 0..1 progress."""
    # Unbuffered: tqdm redraws with a carriage return and no newline, which a buffered child would hold back until exit.
    process = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env={**os.environ, "PYTHONUNBUFFERED": "1"}
    )
    done, last, reported, pending, tail = 0, 0.0, None, b"", b""
    assert process.stderr is not None
    while chunk := process.stderr.read1(4096):
        tail = (tail + chunk)[-4096:]
        *lines, pending = re.split(rb"[\r\n]", pending + chunk)
        # tqdm writes "\r<bar>", so the newest bar is the unterminated tail; it is read again once complete.
        for line in (*lines, pending):
            match = _TQDM.search(line)
            if not match or float(match[2]) <= 0:
                continue
            fraction = min(1.0, float(match[1]) / float(match[2]))
            if fraction < last - 0.5:  # a new bar: the next model pass started
                done += 1
            last = fraction
            value = min(1.0, (done + fraction) / passes)
            if progress is not None and value != reported:
                reported = value
                progress(value)
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


class StemSeparator:
    """Run the chosen model and return its float WAV stems."""

    def __init__(self, model_dir: Path | None = None) -> None:
        # Weights that audio-separator downloads; Demucs keeps its own in the torch hub cache.
        self.model_dir = model_dir or Path.home() / "Audio Stems" / "models"

    def available_models(self) -> tuple[SeparatorModel, ...]:
        # The capability endpoint is intentionally independent of loading weights.
        return tuple(SeparatorModel(name, m.stems, m.label, m.description) for name, m in _MODELS.items())

    def model_stems(self, model_name: str) -> tuple[str, ...]:
        if model_name not in _MODELS:
            raise AdapterError("Unsupported separation model", FailureCategory.INVALID_INPUT)
        return _MODELS[model_name].stems

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
        model = _MODELS[options.model_name]
        needed = decoded.stat().st_size * (len(model.stems) + 1)
        free = shutil.disk_usage(output_dir).free
        if free < needed:
            decoded.unlink(missing_ok=True)
            raise disk_full_error(needed, free)
        # Float output without per-stem rescaling, which would shift the balance; storage scales
        # all stems by one gain instead.
        if model.engine == "demucs":
            command = [
                sys.executable, "-m", "demucs", "-n", options.model_name,
                "-d", options.device, "-o", str(output_dir), "--float32", "--clip-mode", "none", str(decoded),
            ]
        else:
            arguments = {"out": str(output_dir / options.model_name), "models": str(self.model_dir),
                         "checkpoint": model.checkpoint, "overlap": model.overlap, "input": str(decoded)}
            command = [sys.executable, "-c", _AUDIO_SEPARATOR, json.dumps(arguments)]
        try:
            run_with_progress(command, model.passes, progress)
        except subprocess.CalledProcessError as exc:
            if shutil.disk_usage(output_dir).free < needed // 2:
                raise disk_full_error(needed, shutil.disk_usage(output_dir).free) from exc
            raise AdapterError(f"Separator exited with status {exc.returncode}", FailureCategory.PERMANENT) from exc
        except FileNotFoundError as exc:
            raise AdapterError("Separator executable is unavailable", FailureCategory.PERMANENT) from exc
        except OSError as exc:
            raise AdapterError("Separator could not be started", FailureCategory.TRANSIENT) from exc
        finally:
            decoded.unlink(missing_ok=True)

        model_output = output_dir / options.model_name
        if model.engine == "demucs":
            # Demucs writes <model>/<track>/<stem>.wav
            candidates = sorted((path for path in model_output.iterdir() if path.is_dir()), key=lambda p: p.name) if model_output.is_dir() else []
            track_dir = next((path for path in candidates if path.name == audio_path.stem), candidates[0] if candidates else None)
            files = {path.stem: path for path in track_dir.glob("*.wav")} if track_dir else {}
        else:
            # audio-separator writes <track>_(Vocals)_<checkpoint>.wav
            files = {path.stem.split("_(", 1)[1].split(")", 1)[0].lower(): path for path in model_output.glob("*_(*)*.wav")}
        artifacts = tuple(StemArtifact(name, files[name]) for name in model.stems if name in files and files[name].is_file())
        if len(artifacts) != len(model.stems):
            raise AdapterError("Separator did not produce all expected stems", FailureCategory.PERMANENT)
        return SeparationResult(artifacts, level)

    def validate_options(self, options: SeparationOptions) -> None:
        if options.model_name not in _MODELS:
            raise AdapterError("Unsupported separation model", FailureCategory.INVALID_INPUT)
        if options.device not in detect_compute_devices():
            raise AdapterError("Unsupported compute device", FailureCategory.INVALID_INPUT)

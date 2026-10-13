"""Demucs command-line adapter; no Demucs imports escape this boundary."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from contracts import (
    AdapterError,
    FailureCategory,
    SeparationOptions,
    SeparationResult,
    SeparatorModel,
    StemArtifact,
    StemSeparator,
)


_STEMS = ("vocals", "drums", "bass", "guitar", "piano", "other")
_MODELS = ("htdemucs_6s",)
_PARAMETERS = {"shifts": "int", "overlap": "float", "segment": "int"}


class DemucsSeparator(StemSeparator):
    """Run Demucs and return the six supported WAV stems."""

    def available_models(self) -> tuple[SeparatorModel, ...]:
        # These are stable Demucs model identifiers supported by its CLI.
        # The capability endpoint is intentionally independent of loading weights.
        return tuple(SeparatorModel(name, _STEMS, _PARAMETERS.copy()) for name in _MODELS)

    def separate(
        self,
        audio_path: Path,
        output_dir: Path,
        options: SeparationOptions,
    ) -> SeparationResult:
        self.validate_options(options)
        output_dir.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable, "-m", "demucs", "-n", options.model_name,
            "-d", options.device, "-o", str(output_dir),
        ]
        for name in ("shifts", "overlap", "segment"):
            if name in options.parameters:
                command.extend((f"--{name}", str(options.parameters[name])))
        command.append(str(audio_path))
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
        if not options.device or options.device not in {"cpu", "cuda", "mps"} and not options.device.startswith("cuda:"):
            raise AdapterError("Unsupported Demucs device", FailureCategory.INVALID_INPUT)
        for name, value in options.parameters.items():
            if name not in _PARAMETERS:
                raise AdapterError(f"Unsupported Demucs parameter: {name}", FailureCategory.INVALID_INPUT)
            if name in {"shifts", "segment"}:
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise AdapterError(f"Demucs {name} must be a positive integer", FailureCategory.INVALID_INPUT)
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise AdapterError("Demucs overlap must be between 0 and 1", FailureCategory.INVALID_INPUT)

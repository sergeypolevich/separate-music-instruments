"""Set up the local runtime environment and launch the app, API, or worker."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

from audio_stems.desktop import missing_runtime_modules


ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
# A copy of the requirements.txt last installed; Start.bat skips all setup while it matches.
INSTALLED_MARKER = VENV_DIR / "requirements.installed"
WORKER = ("-m", "audio_stems.pipeline")
API = ("-m", "audio_stems.api")
DESKTOP = ("-m", "audio_stems.desktop")
USAGE = "Usage: py run.py [--worker | --api | --app]"


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def in_project_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == VENV_DIR.resolve()
    except OSError:
        return False


def ensure_project_venv() -> Path:
    python = venv_python()
    if not python.exists():
        print("Creating the project's isolated Python environment (.venv)...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
    return python


def requirements_installed() -> bool:
    try:
        return INSTALLED_MARKER.read_bytes() == REQUIREMENTS.read_bytes()
    except OSError:
        return False


def install_runtime_dependencies() -> None:
    missing = missing_runtime_modules()
    if not missing and requirements_installed():
        print("Runtime dependencies are already installed.")
        return

    print("Installing runtime dependencies" + (": " + ", ".join(missing) if missing else " from requirements.txt"))
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)],
        check=True,
        cwd=ROOT,
    )
    shutil.copyfile(REQUIREMENTS, INSTALLED_MARKER)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments not in ([], ["--worker"], ["--api"], ["--app"]):
        print(USAGE)
        return 2
    target = (
        WORKER if arguments == ["--worker"] else
        API if arguments == ["--api"] else
        DESKTOP
    )
    if not in_project_venv():
        python = ensure_project_venv()
        subprocess.run([str(python), str(Path(__file__).resolve()), *arguments], check=True, cwd=ROOT)
        return 0

    try:
        install_runtime_dependencies()
    except subprocess.CalledProcessError as exc:
        print(f"Dependency installation failed (exit code {exc.returncode}); application was not started.")
        return exc.returncode or 1

    return subprocess.run([sys.executable, *target], cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())

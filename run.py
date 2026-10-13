"""Set up the local runtime environment and launch the CLI or worker."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
APP = ROOT / "download_tracks.py"
WORKER = ROOT / "worker.py"
RUNTIME_MODULES = ("pytubefix", "demucs")


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


def missing_runtime_modules() -> list[str]:
    return [name for name in RUNTIME_MODULES if importlib.util.find_spec(name) is None]


def install_runtime_dependencies() -> None:
    missing = missing_runtime_modules()
    if not missing:
        print("Runtime dependencies are already installed.")
        return

    print("Installing missing runtime dependencies: " + ", ".join(missing))
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)],
        check=True,
        cwd=ROOT,
    )


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments not in ([], ["--worker"]):
        print("Usage: py run.py [--worker]")
        return 2
    target = WORKER if arguments == ["--worker"] else APP
    if not in_project_venv():
        python = ensure_project_venv()
        subprocess.run([str(python), str(Path(__file__).resolve()), *arguments], check=True, cwd=ROOT)
        return 0

    try:
        install_runtime_dependencies()
    except subprocess.CalledProcessError as exc:
        print(f"Dependency installation failed (exit code {exc.returncode}); application was not started.")
        return exc.returncode or 1

    return subprocess.run([sys.executable, str(target)], cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())

"""Set up the local runtime environment and launch the app, API, or worker."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

from audio_stems.desktop import missing_runtime_modules


ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
# Every package with its exact version and hash, per platform; generated from requirements.txt.
REQUIREMENTS = ROOT / ("requirements-windows.lock" if os.name == "nt" else "requirements-macos.lock")
# A copy of the lock file last installed; Start Windows.bat and Start MacOS.command skip all setup while it matches.
INSTALLED_MARKER = VENV_DIR / "requirements.installed"
WORKER = ("-m", "audio_stems.pipeline")
API = ("-m", "audio_stems.api")
DESKTOP = ("-m", "audio_stems.desktop")
USAGE = "Usage: py run.py [--worker | --api | --app]"
STEPS = 4  # 1 Python (the launcher), 2 the environment, 3 the libraries, 4 the start
QUIET_SECONDS = 10.0  # a line at least this often during setup, so it never looks stuck
_PROGRESS = re.compile(r"^Progress (\d+) of (\d+)$")


def step(number: int, text: str) -> None:
    """A numbered setup step, in the window and its title."""
    print(f"\n[{number}/{STEPS}] {text}", flush=True)
    title = f"Audio Stems - step {number} of {STEPS}: {text}"
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    elif sys.stdout.isatty():
        print(f"\033]0;{title}\007", end="", flush=True)


def run_showing_progress(command: list[str], *, cwd: Path, out=None, quiet=QUIET_SECONDS) -> None:
    """Run pip with its raw progress: its lines as they come, a download as one line «  12 / 37 MB» kept up to date,
    and «  still working... 2:30» after `quiet` seconds without news (unpacking torch says nothing for minutes)."""
    out = out or sys.stdout
    started = last = time.monotonic()
    lock, done, shown = threading.Lock(), threading.Event(), {"open": False, "mb": None}

    def say(text: str, *, same_line: bool = False) -> None:
        nonlocal last
        with lock:
            if shown["open"] and not same_line:
                out.write("\n")
            out.write(("\r" if same_line else "") + text + ("" if same_line else "\n"))
            out.flush()
            shown["open"], last = same_line, time.monotonic()

    def heartbeat() -> None:
        while not done.wait(min(quiet, 1.0)):
            if time.monotonic() - last >= quiet:
                elapsed = int(time.monotonic() - started)
                say(f"  still working... {elapsed // 60}:{elapsed % 60:02d}")

    process = subprocess.Popen([*command, "--progress-bar", "raw"], cwd=cwd, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    beat = threading.Thread(target=heartbeat, daemon=True)
    beat.start()
    try:
        for line in process.stdout:
            line = line.rstrip()
            if match := _PROGRESS.match(line):
                done_mb, total_mb = int(match[1]) // 1_000_000, int(match[2]) // 1_000_000
                if total_mb and (done_mb, total_mb) != shown["mb"]:
                    shown["mb"] = done_mb, total_mb
                    say(f"  {done_mb} / {total_mb} MB", same_line=True)
            elif line:
                say(line)
    finally:
        code = process.wait()
        done.set()
        beat.join()
    if shown["open"]:
        out.write("\n")
    if code:
        raise subprocess.CalledProcessError(code, command)


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
        step(2, "Creating the app's own Python environment (.venv, a few seconds)")
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

    step(3, "Downloading and installing the libraries (about 350 MB, the longest step)")
    if missing:
        print("Missing: " + ", ".join(missing))
    started = time.monotonic()
    run_showing_progress([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--require-hashes", "-r", str(REQUIREMENTS)], cwd=ROOT)
    shutil.copyfile(REQUIREMENTS, INSTALLED_MARKER)
    elapsed = int(time.monotonic() - started)
    print(f"Libraries installed in {elapsed // 60}:{elapsed % 60:02d}.")


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

    if arguments in ([], ["--app"]):
        step(4, "Starting Audio Stems: the browser opens by itself")
    return subprocess.run([sys.executable, *target], cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())

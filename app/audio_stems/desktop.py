"""Supervise the local API and worker for the one-click Windows launcher."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

from audio_stems.config import Settings


ROOT = Path(__file__).resolve().parents[1]
API = ("-m", "audio_stems.api")
WORKER = ("-m", "audio_stems.pipeline")
# Checked by name only (find_spec, no import), so the check on every start costs milliseconds.
RUNTIME_MODULES = ("pytubefix", "demucs", "fastapi", "uvicorn", "imageio_ffmpeg", "soundfile", "audio_separator", "beat_this")
MISSING_DEPENDENCIES = 3  # exit code: Start.bat then runs the full dependency setup


def missing_runtime_modules() -> list[str]:
    return [name for name in RUNTIME_MODULES if importlib.util.find_spec(name) is None]


def wait_for_api(
    port: int,
    *,
    api_process: subprocess.Popen | None = None,
    timeout: float = 60,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    deadline = time.monotonic() + timeout
    health_url = f"http://127.0.0.1:{port}/api/v1/health"
    while time.monotonic() < deadline:
        if api_process is not None and api_process.poll() is not None:
            raise RuntimeError("The local API stopped before it became ready")
        try:
            with urlopen(health_url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            pass
        sleep(0.2)
    raise TimeoutError("The local API did not become ready within 60 seconds")


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run(
    *,
    stop_file: Path | None = None,
    popen: Callable = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    open_browser: Callable | None = None,
) -> int:
    missing = missing_runtime_modules()
    if missing:
        print("Missing dependencies: " + ", ".join(missing), flush=True)
        return MISSING_DEPENDENCIES
    settings = Settings.load()
    url = f"http://127.0.0.1:{settings.api_port}"
    if stop_file is None:
        configured_stop_file = os.environ.get("AUDIO_STEMS_STOP_FILE")
        stop_file = Path(configured_stop_file) if configured_stop_file else None

    processes: list[subprocess.Popen] = []
    browser_opener = open_browser or webbrowser.open
    try:
        print("Starting the local API and background worker...", flush=True)
        api_process = popen([sys.executable, *API], cwd=ROOT)
        processes.append(api_process)
        worker_process = popen([sys.executable, *WORKER], cwd=ROOT)
        processes.append(worker_process)
        wait_for_api(settings.api_port, api_process=api_process)
        print(f"Audio Stems is ready at {url}", flush=True)
        if os.environ.get("AUDIO_STEMS_OPEN_BROWSER", "1").lower() not in {"0", "false", "no"}:
            try:
                if not browser_opener(url, new=2):
                    print(f"Open this address in your browser: {url}", flush=True)
            except Exception as exc:
                print(f"Could not open the browser automatically: {exc}\nOpen: {url}", flush=True)

        while True:
            completed = [process for process in processes if process.poll() is not None]
            if completed:
                return next((process.returncode for process in completed if process.returncode), 0)
            if stop_file is not None and stop_file.exists():
                return 0
            sleep(0.25)
    except KeyboardInterrupt:
        return 0
    finally:
        for process in reversed(processes):
            _stop_process(process)
        print("Audio Stems services stopped.", flush=True)


def disable_quick_edit(kernel32=None) -> bool:
    """Turn off QuickEdit in the launcher's console window. With it, one click in the window starts a
    text selection that blocks every process writing to the console: the API froze on a log line."""
    if kernel32 is None:
        if os.name != "nt":
            return False
        import ctypes
        kernel32 = ctypes.windll.kernel32
    import ctypes
    handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE: the console mode lives on the input buffer
    mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False  # no console (started without a window)
    # Drop ENABLE_QUICK_EDIT_MODE (0x40); ENABLE_EXTENDED_FLAGS (0x80) makes the change take effect.
    return bool(kernel32.SetConsoleMode(handle, (mode.value & ~0x40) | 0x80))


if __name__ == "__main__":
    disable_quick_edit()
    raise SystemExit(run())

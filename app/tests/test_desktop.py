from pathlib import Path
import shutil
import subprocess

import pytest

import audio_stems.desktop as desktop
class FakeProcess:
    def __init__(self, args, **_kwargs):
        self.args = args
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_supervisor_starts_api_and_worker_then_opens_local_browser(tmp_path, monkeypatch):
    stop_file = tmp_path / "stop"
    stop_file.touch()
    processes = []
    opened = []

    def popen(args, **kwargs):
        process = FakeProcess(args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(desktop, "wait_for_api", lambda *_args, **_kwargs: None)

    result = desktop.run(
        stop_file=stop_file,
        popen=popen,
        sleep=lambda _seconds: None,
        open_browser=lambda url, **_kwargs: opened.append(url) or True,
    )

    assert result == 0
    assert processes[0].args[1:] == list(desktop.API)
    assert processes[1].args[1:] == list(desktop.WORKER)
    assert opened == ["http://127.0.0.1:8000"]
    assert all(process.terminated for process in processes)


def test_supervisor_closes_api_if_worker_cannot_start(monkeypatch):
    api = FakeProcess(["api"])
    calls = 0

    def popen(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return api
        raise OSError("worker could not start")

    monkeypatch.setattr(desktop, "wait_for_api", lambda *_args, **_kwargs: None)

    try:
        desktop.run(stop_file=Path("unused"), popen=popen, sleep=lambda _seconds: None)
    except OSError as exc:
        assert str(exc) == "worker could not start"
    else:
        raise AssertionError("worker startup error should be surfaced")

    assert api.terminated


def test_windows_one_click_launcher_uses_pinned_official_python_installer():
    root = Path(__file__).parents[1]
    bootstrap = (root / "Start-Audio-Stems.ps1").read_text(encoding="utf-8")
    button = (root.parent / "Start.bat").read_text(encoding="utf-8")  # the launcher sits in the repository root

    assert "python-3.12.10-amd64.exe" in bootstrap
    assert "python.org/ftp/python/3.12.10/" in bootstrap
    assert "Get-AuthenticodeSignature" in bootstrap
    assert "Python Software Foundation" in bootstrap
    assert "InstallAllUsers=0" in bootstrap
    assert "run.py" in bootstrap and "--app" in bootstrap
    assert r'set "APP=%~dp0app"' in button and r'"%APP%\Start-Audio-Stems.ps1"' in button


def test_launcher_fast_path_skips_setup_only_while_installed_requirements_match():
    button = (Path(__file__).parents[2] / "Start.bat").read_text(encoding="utf-8")

    fast_check = button.index(r'fc /b "%APP%\requirements-windows.lock" "%APP%\.venv\requirements.installed"')
    assert fast_check < button.index("-m audio_stems.desktop") < button.index(":setup")
    assert "|| goto setup" in button
    # Exit code 3 (a dependency is missing) drops the marker and falls back to the full setup.
    missing = button.index('if "%CODE%"=="3"')
    assert button.index(r'del "%APP%\.venv\requirements.installed"', missing) < button.index("goto setup", missing)
    assert desktop.MISSING_DEPENDENCIES == 3


def test_supervisor_exits_with_code_3_without_starting_services_when_a_module_is_missing(monkeypatch):
    started = []
    monkeypatch.setattr(desktop.importlib.util, "find_spec", lambda name: None if name == "imageio_ffmpeg" else object())

    assert desktop.run(popen=lambda *args, **kwargs: started.append(args)) == desktop.MISSING_DEPENDENCIES
    assert started == []


def test_supervisor_returns_the_exit_code_of_a_crashed_service_and_stops_the_other(monkeypatch):
    processes = []

    def popen(args, **_kwargs):
        process = FakeProcess(args)
        processes.append(process)
        return process

    def worker_crashes(_seconds):
        processes[1].returncode = 3

    monkeypatch.setattr(desktop, "wait_for_api", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("AUDIO_STEMS_OPEN_BROWSER", "0")

    result = desktop.run(stop_file=None, popen=popen, sleep=worker_crashes, open_browser=lambda *_a, **_k: True)

    assert result == 3
    assert processes[0].terminated  # the API is stopped when the worker dies


def test_wait_for_api_fails_fast_when_the_api_process_exits(monkeypatch):
    exited = FakeProcess(["api"])
    exited.returncode = 1
    try:
        desktop.wait_for_api(1, api_process=exited, sleep=lambda _seconds: None)
    except RuntimeError as error:
        assert "stopped before it became ready" in str(error)
    else:
        raise AssertionError("wait_for_api should fail when the API process has exited")


def test_services_start_from_the_project_root_with_package_modules():
    root = Path(desktop.__file__).resolve().parents[1]
    assert desktop.ROOT == root
    assert (root / "audio_stems" / "api.py").is_file() and (root / "web" / "index.html").is_file()
    assert desktop.API == ("-m", "audio_stems.api") and desktop.WORKER == ("-m", "audio_stems.pipeline")


def _mac_tree(tmp_path, desktop_exit, *, marker=True):
    """A fake checkout for Start.command: a Python stand-in that exits with `desktop_exit`, and a setup
    script that only records that it ran."""
    repo = Path(__file__).parents[2]
    root = tmp_path / "Audio Stems"
    app = root / "app"
    (app / ".venv" / "bin").mkdir(parents=True)
    # Git may check scripts out with CRLF on Windows; macOS gets LF from the repository.
    (root / "Start.command").write_bytes((repo / "Start.command").read_bytes().replace(b"\r\n", b"\n"))
    (app / "requirements-macos.lock").write_text("demucs==4.1.0\n", encoding="utf-8")
    if marker:
        (app / ".venv" / "requirements.installed").write_text("demucs==4.1.0\n", encoding="utf-8")
    python = app / ".venv" / "bin" / "python"
    python.write_text(f'#!/bin/sh\necho "desktop $*" >> "{(tmp_path / "log").as_posix()}"\nexit {desktop_exit}\n', encoding="utf-8", newline="\n")
    python.chmod(0o755)
    (app / "Start-Audio-Stems.sh").write_text(f'echo setup >> "{(tmp_path / "log").as_posix()}"\n', encoding="utf-8", newline="\n")
    return root


@pytest.mark.parametrize(("desktop_exit", "marker", "expected"), [
    (0, True, ["desktop -m audio_stems.desktop"]),               # fast path
    (3, True, ["desktop -m audio_stems.desktop", "setup"]),      # a module went missing
    (0, False, ["setup"]),                                       # requirements changed or first start
])
def test_mac_launcher_takes_the_fast_path_only_while_requirements_match(tmp_path, desktop_exit, marker, expected):
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("no POSIX shell here")
    root = _mac_tree(tmp_path, desktop_exit, marker=marker)

    subprocess.run([sh, (root / "Start.command").as_posix()], check=False, timeout=30)

    assert (tmp_path / "log").read_text(encoding="utf-8").split("\n")[:-1] == expected
    assert (root / "app" / ".venv" / "requirements.installed").exists() is (marker and desktop_exit != 3)


def test_mac_setup_pins_uv_and_checks_its_checksum_for_both_mac_architectures():
    setup = (Path(__file__).parents[1] / "Start-Audio-Stems.sh").read_text(encoding="utf-8")

    assert "UV_VERSION=0.12.22" in setup and "shasum -a 256 -c" in setup
    assert "arm64) ARCH=aarch64; SUM=5d714de09501a59393ceca78f4bc232a50478729640d251907160299b2a93ddd" in setup
    assert "x86_64) ARCH=x86_64; SUM=1b8a5b316883df2daf20fb9a446e5b230e01d947d57aba2694977c5ac5a7e98c" in setup
    assert 'exec "$PYTHON" run.py --app' in setup and "sudo" not in setup
    assert "export UV_PYTHON_PREFERENCE=only-managed" in setup  # uv installs its own 3.12


def test_launcher_console_turns_off_quick_edit_so_a_click_cannot_freeze_the_app():
    class Kernel32:
        def __init__(self, has_console=True):
            self.has_console, self.set_to = has_console, None

        def GetStdHandle(self, which):
            assert which == -10  # the console input buffer holds the mode
            return 7

        def GetConsoleMode(self, handle, mode_ref):
            mode_ref._obj.value = 0x1F7  # Windows default: QuickEdit (0x40) on
            return self.has_console

        def SetConsoleMode(self, handle, mode):
            self.set_to = mode
            return 1

    kernel32 = Kernel32()
    assert desktop.disable_quick_edit(kernel32) is True
    assert kernel32.set_to & 0x40 == 0 and kernel32.set_to & 0x80  # QuickEdit off, extended flags on
    assert kernel32.set_to & ~0xC0 == 0x1F7 & ~0xC0  # the other input modes are kept
    no_console = Kernel32(has_console=False)
    assert desktop.disable_quick_edit(no_console) is False and no_console.set_to is None

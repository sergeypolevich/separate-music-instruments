from pathlib import Path

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

    fast_check = button.index(r'fc /b "%APP%\requirements.txt" "%APP%\.venv\requirements.installed"')
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

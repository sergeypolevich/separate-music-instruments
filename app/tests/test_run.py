from pathlib import Path

import pytest

import run


def test_installs_requirements_when_runtime_module_is_missing(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(run, "INSTALLED_MARKER", tmp_path / "requirements.installed")
    monkeypatch.setattr(run, "missing_runtime_modules", lambda: ["demucs"])
    monkeypatch.setattr(run.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    run.install_runtime_dependencies()

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == [run.sys.executable, "-m", "pip", "install", "--require-hashes", "-r", str(run.REQUIREMENTS)]
    assert kwargs["check"] is True
    assert kwargs["cwd"] == run.ROOT


def test_does_not_run_pip_when_runtime_modules_are_installed(monkeypatch):
    monkeypatch.setattr(run, "missing_runtime_modules", lambda: [])
    monkeypatch.setattr(run, "requirements_installed", lambda: True)

    def unexpected_pip(*_args, **_kwargs):
        raise AssertionError("pip should not run when dependencies are present")

    monkeypatch.setattr(run.subprocess, "run", unexpected_pip)
    run.install_runtime_dependencies()


def test_changed_requirements_are_installed_and_recorded_for_the_fast_path(monkeypatch, tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("demucs==9\n", encoding="utf-8")
    marker = tmp_path / "requirements.installed"
    marker.write_text("demucs==8\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(run, "REQUIREMENTS", requirements)
    monkeypatch.setattr(run, "INSTALLED_MARKER", marker)
    monkeypatch.setattr(run, "missing_runtime_modules", lambda: [])
    monkeypatch.setattr(run.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    assert not run.requirements_installed()
    run.install_runtime_dependencies()

    assert len(calls) == 1
    assert marker.read_text(encoding="utf-8") == "demucs==9\n" and run.requirements_installed()


def test_virtual_environment_python_path_is_platform_specific(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "VENV_DIR", tmp_path / ".venv")
    monkeypatch.setattr(run.os, "name", "nt")

    assert run.venv_python() == tmp_path / ".venv" / "Scripts" / "python.exe"


def test_worker_mode_launches_worker_script_after_dependency_check(monkeypatch):
    calls = []
    monkeypatch.setattr(run, "in_project_venv", lambda: True)
    monkeypatch.setattr(run, "install_runtime_dependencies", lambda: None)
    monkeypatch.setattr(
        run.subprocess, "run",
        lambda args, **kwargs: calls.append((args, kwargs)) or type("Result", (), {"returncode": 0})(),
    )

    assert run.main(["--worker"]) == 0
    assert calls[0][0] == [run.sys.executable, *run.WORKER]


def test_api_mode_launches_local_api_script(monkeypatch):
    calls = []
    monkeypatch.setattr(run, "in_project_venv", lambda: True)
    monkeypatch.setattr(run, "install_runtime_dependencies", lambda: None)
    monkeypatch.setattr(
        run.subprocess, "run",
        lambda args, **kwargs: calls.append(args) or type("Result", (), {"returncode": 0})(),
    )

    assert run.main(["--api"]) == 0
    assert calls == [[run.sys.executable, *run.API]]


def test_launcher_rejects_unknown_arguments(monkeypatch):
    monkeypatch.setattr(run, "in_project_venv", lambda: True)

    assert run.main(["--unknown"]) == 2


@pytest.mark.parametrize("arguments", [["--app"], []], ids=["app-flag", "no-arguments"])
def test_app_mode_launches_supervisor_after_dependency_check(monkeypatch, arguments):
    calls = []
    monkeypatch.setattr(run, "in_project_venv", lambda: True)
    monkeypatch.setattr(run, "install_runtime_dependencies", lambda: None)
    monkeypatch.setattr(
        run.subprocess, "run",
        lambda args, **kwargs: calls.append((args, kwargs)) or type("Result", (), {"returncode": 0})(),
    )

    assert run.main(arguments) == 0
    assert calls[0][0] == [run.sys.executable, *run.DESKTOP]


def test_launcher_usage_includes_app_mode():
    assert "--app" in run.USAGE


def test_outside_the_venv_it_creates_the_project_venv_and_reruns_itself(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(run, "in_project_venv", lambda: False)
    monkeypatch.setattr(run, "VENV_DIR", tmp_path / ".venv")
    monkeypatch.setattr(run.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))

    assert run.main(["--app"]) == 0

    venv_python = run.venv_python()
    assert calls[0][0] == [run.sys.executable, "-m", "venv", str(tmp_path / ".venv")]
    assert calls[1][0] == [str(venv_python), str(Path(run.__file__).resolve()), "--app"]
    assert calls[1][1]["cwd"] == run.ROOT


def test_project_paths_are_next_to_the_launcher():
    root = Path(run.__file__).resolve().parent
    assert run.ROOT == root
    assert run.VENV_DIR == root / ".venv"
    lock = "requirements-windows.lock" if run.os.name == "nt" else "requirements-macos.lock"
    assert run.REQUIREMENTS == root / lock and run.REQUIREMENTS.is_file()


def test_failed_dependency_install_stops_before_starting_the_app(monkeypatch):
    def failing_install():
        raise run.subprocess.CalledProcessError(7, ["pip"])

    started = []
    monkeypatch.setattr(run, "in_project_venv", lambda: True)
    monkeypatch.setattr(run, "install_runtime_dependencies", failing_install)
    monkeypatch.setattr(run.subprocess, "run", lambda *args, **kwargs: started.append(args))

    assert run.main(["--app"]) == 7
    assert started == []


@pytest.mark.parametrize("lock", ["requirements-windows.lock", "requirements-macos.lock"])
def test_lock_files_pin_every_direct_requirement_and_hash_every_package(lock):
    root = Path(run.__file__).resolve().parent
    direct = {line for line in (root / "requirements.txt").read_text(encoding="utf-8").split() if "==" in line}
    lines = (root / lock).read_text(encoding="utf-8").replace("\\\n", " ").splitlines()
    entries = [line for line in lines if line and not line.startswith("#")]
    assert direct <= {entry.split()[0] for entry in entries}  # regenerate the lock files after changing requirements.txt
    for entry in entries:
        assert "==" in entry.split()[0] and "--hash=sha256:" in entry, entry

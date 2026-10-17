import pytest

import run


def test_installs_requirements_when_runtime_module_is_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(run, "missing_runtime_modules", lambda: ["demucs"])
    monkeypatch.setattr(run.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    run.install_runtime_dependencies()

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == [run.sys.executable, "-m", "pip", "install", "-r", str(run.REQUIREMENTS)]
    assert kwargs["check"] is True
    assert kwargs["cwd"] == run.ROOT


def test_does_not_run_pip_when_runtime_modules_are_installed(monkeypatch):
    monkeypatch.setattr(run, "missing_runtime_modules", lambda: [])

    def unexpected_pip(*_args, **_kwargs):
        raise AssertionError("pip should not run when dependencies are present")

    monkeypatch.setattr(run.subprocess, "run", unexpected_pip)
    run.install_runtime_dependencies()


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
    assert calls[0][0] == [run.sys.executable, str(run.WORKER)]


def test_api_mode_launches_local_api_script(monkeypatch):
    calls = []
    monkeypatch.setattr(run, "in_project_venv", lambda: True)
    monkeypatch.setattr(run, "install_runtime_dependencies", lambda: None)
    monkeypatch.setattr(
        run.subprocess, "run",
        lambda args, **kwargs: calls.append(args) or type("Result", (), {"returncode": 0})(),
    )

    assert run.main(["--api"]) == 0
    assert calls == [[run.sys.executable, str(run.API)]]


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
    assert calls[0][0] == [run.sys.executable, str(run.DESKTOP)]


def test_launcher_usage_includes_app_mode():
    assert "--app" in run.USAGE

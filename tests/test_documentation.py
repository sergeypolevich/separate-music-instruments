from pathlib import Path


def test_readme_documents_windows_setup_runtime_processes_and_maintenance():
    readme = Path(__file__).parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")

    for required in (
        "py run.py",
        "py run.py --api",
        "py run.py --worker",
        "py -m pytest",
        "Backup and restore",
        "Maintenance",
        "audio_stems.db",
        "YouTube Audio",
        "RUN_LIVE_E2E",
    ):
        assert required in text


def test_readme_describes_shared_api_worker_and_restart_limits():
    readme = Path(__file__).parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")

    assert "Keep `py run.py --worker` running separately" in text
    assert "restart both the API and worker" in text
    assert "By default, no live YouTube or Demucs smoke tests run" in text

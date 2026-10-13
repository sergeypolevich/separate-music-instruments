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
        "Start Audio Stems.bat",
        "py run.py --app",
    ):
        assert required in text


def test_readme_describes_shared_api_worker_and_restart_limits():
    readme = Path(__file__).parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")

    assert "Keep `py run.py --worker` running separately" in text
    assert "restart both the API and worker" in text
    assert "By default, no live YouTube or Demucs smoke tests run" in text
    assert "Run it before every commit" in text


def test_project_skill_requires_visible_playwright_song2_browser_gate():
    root = Path(__file__).parents[1]
    skill = (root / "skills" / "audio-stems-development.md").read_text(encoding="utf-8")
    requirements = (root / "requirements-test.txt").read_text(encoding="utf-8")

    assert "Before every commit" in skill
    assert "Playwright" in skill
    assert "visible window" in skill
    assert "playwright" in requirements.lower()

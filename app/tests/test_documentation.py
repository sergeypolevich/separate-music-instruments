from pathlib import Path


def test_readme_documents_windows_setup_runtime_processes_and_maintenance():
    readme = Path(__file__).parents[1] / "README.md"  # the developer guide; the root READMEs are for users
    text = readme.read_text(encoding="utf-8")

    for required in (
        "py run.py",
        "py run.py --api",
        "py run.py --worker",
        "py -m pytest",
        "Backup and restore",
        "Maintenance",
        "audio_stems.db",
        "Music",
        "RUN_LIVE_E2E",
        "Start.bat",
        "cd app",
        "py run.py --app",
    ):
        assert required in text


def test_readme_describes_shared_api_worker_and_restart_limits():
    readme = Path(__file__).parents[1] / "README.md"  # the developer guide; the root READMEs are for users
    text = readme.read_text(encoding="utf-8")

    assert "Keep `py run.py --worker` running separately" in text
    assert "restart both the API and worker" in text
    assert "By default, no live YouTube or Demucs smoke tests run" in text
    assert "Run it before every commit" in text


def test_user_readmes_explain_download_and_start_in_three_languages():
    root = Path(__file__).parents[2]
    for name, words in (("README.md", ("Download ZIP", "Phones and tablets are not supported")),
                        ("README.ru.md", ("Download ZIP", "Телефоны и планшеты не поддерживаются")),
                        ("README.de.md", ("Download ZIP", "Smartphones und Tablets werden nicht unterstützt"))):
        text = (root / name).read_text(encoding="utf-8")
        others = {"README.md", "README.ru.md", "README.de.md"} - {name}  # links to the other languages
        for required in ("Start.bat", "Start.command", "app/README.md", "(LICENSE)", *others, *words):
            assert required in text, (name, required)
        assert "py run.py" not in text  # developer commands live in app/README.md
    assert (root / "LICENSE").read_text(encoding="utf-8").startswith("MIT License\n\nCopyright (c) 2026 Sergey Polevich")


def test_project_skill_requires_visible_playwright_browser_gate():
    root = Path(__file__).parents[1]
    skill = (root / "skills" / "audio-stems-development.md").read_text(encoding="utf-8")
    requirements = (root / "requirements-test.txt").read_text(encoding="utf-8")
    runtime_requirements = (root / "requirements.txt").read_text(encoding="utf-8")

    assert "Before every commit" in skill
    assert "Playwright" in skill
    assert "visible window" in skill
    assert "playwright" in requirements.lower()
    assert "numpy==2.5.4" in runtime_requirements

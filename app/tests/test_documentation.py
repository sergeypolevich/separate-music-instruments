import re
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
        for server in ("youtube.com", "pypi.org", "huggingface.co", "cloud.cp.jku.at", "api.github.com"):
            assert server in text, (name, server)  # every host the app or its launcher contacts
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


def test_vendored_web_code_ships_with_its_license_text():
    vendor = Path(__file__).parents[1] / "web" / "vendor"
    stretch = (vendor / "signalsmith-stretch-1.3.2.mjs").read_text(encoding="utf-8")
    license_text = (vendor / "LICENSE-signalsmith-stretch.txt").read_text(encoding="utf-8")

    assert "LICENSE-signalsmith-stretch.txt" in stretch.splitlines()[2]
    assert license_text.startswith("MIT License") and "Geraint Luff" in license_text
    assert "The above copyright notice and this permission notice shall be included" in license_text


def test_third_party_list_names_every_model_and_direct_dependency():
    from audio_stems.separator import _WEIGHTS

    root = Path(__file__).parents[2]
    listed = (root / "THIRD_PARTY.md").read_text(encoding="utf-8")
    direct = [line.split("==")[0] for line in (root / "app" / "requirements.txt").read_text(encoding="utf-8").splitlines()
              if "==" in line and not line.startswith("#")]

    assert direct and all(name in listed for name in direct)
    assert all(model in re.sub(r"[ -]", "_", listed.lower()) for model in _WEIGHTS)
    assert "beat_this `final0`" in listed and "CC BY-NC 4.0" in listed

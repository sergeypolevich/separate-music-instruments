import json
import subprocess

import pytest
import asyncio
from dataclasses import replace
import httpx

from api import create_app
from demucs_adapter import DemucsSeparator
from metadata import SqliteMetadataStore
from settings import Settings
from sources import YouTubeSourceAdapter
from updates import DependencyUpdater, GitHubReleaseClient, UpdateCheckError, UpdateService


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return json.dumps(self.data).encode("utf-8")


def release(tag="v2.1.0", *, prerelease=False):
    return {
        "tag_name": tag,
        "name": f"Release {tag}",
        "body": "Bug fixes and improvements",
        "html_url": f"https://github.com/sergeypolevich/separate-music-instruments/releases/tag/{tag}",
        "published_at": "2026-10-01T10:00:00Z",
        "draft": False,
        "prerelease": prerelease,
    }


def test_github_release_client_uses_fixed_project_and_filters_drafts_and_prereleases():
    calls = []
    client = GitHubReleaseClient(open_url=lambda request, timeout: (
        calls.append((request, timeout)) or FakeResponse([
            release(), release("v3.0.0-beta", prerelease=True), {**release("v1.0.0"), "draft": True}
        ])
    ))

    result = client.fetch_releases()

    assert [item.tag_name for item in result] == ["v2.1.0"]
    assert result[0].notes == "Bug fixes and improvements"
    request, timeout = calls[0]
    assert request.full_url == "https://api.github.com/repos/sergeypolevich/separate-music-instruments/releases?per_page=10"
    assert request.get_header("User-agent")
    assert timeout > 0


def test_release_client_rejects_untrusted_release_urls_and_invalid_payloads():
    bad_url = GitHubReleaseClient(open_url=lambda *_args, **_kwargs: FakeResponse([
        {**release(), "html_url": "https://evil.example/update"}
    ]))
    invalid = GitHubReleaseClient(open_url=lambda *_args, **_kwargs: FakeResponse({"unexpected": True}))

    with pytest.raises(ValueError, match="trusted GitHub"):
        bad_url.fetch_releases()
    with pytest.raises(ValueError, match="release list"):
        invalid.fetch_releases()


def test_update_requires_explicit_confirmation_before_contacting_github():
    class NeverFetch:
        def fetch_releases(self):
            raise AssertionError("must not inspect releases without confirmation")

    class NeverInstall:
        def install(self):
            raise AssertionError("must not install without confirmation")

    service = UpdateService(NeverFetch(), NeverInstall())
    with pytest.raises(PermissionError, match="confirmation"):
        service.apply("v2.1.0", confirmed=False)


def test_update_service_wraps_github_network_failures_in_a_stable_error():
    class Offline:
        def fetch_releases(self):
            raise OSError("network unavailable")

    with pytest.raises(UpdateCheckError):
        UpdateService(Offline(), object()).check()


def test_update_installs_only_the_declared_requirements_file_and_reports_restart(tmp_path):
    root = tmp_path / "application"
    root.mkdir()
    requirements = root / "requirements.txt"
    requirements.write_text("example==1.0\n", encoding="utf-8")
    commands = []
    updater = DependencyUpdater(
        root, requirements, python="python-test",
        runner=lambda command, **kwargs: commands.append((command, kwargs)),
    )

    updater.install()

    command, kwargs = commands[0]
    assert command == ["python-test", "-m", "pip", "install", "--upgrade", "-r", str(requirements)]
    assert kwargs["cwd"] == root
    assert kwargs["check"] is True
    assert kwargs["timeout"] == 1800


def test_updater_rejects_requirements_outside_application_root(tmp_path):
    root = tmp_path / "application"
    root.mkdir()
    external = tmp_path / "requirements.txt"
    with pytest.raises(ValueError, match="only accepts"):
        DependencyUpdater(root, external)


def test_update_only_accepts_latest_stable_release_and_propagates_install_failure():
    class Installer:
        def install(self):
            raise subprocess.CalledProcessError(1, "pip")

    service = UpdateService(
        GitHubReleaseClient(open_url=lambda *_args, **_kwargs: FakeResponse([release(), release("v2.0.0")])),
        Installer(),
    )

    with pytest.raises(ValueError, match="latest stable"):
        service.apply("v2.0.0", confirmed=True)
    with pytest.raises(subprocess.CalledProcessError):
        service.apply("v2.1.0", confirmed=True)


def test_update_api_checks_release_notes_and_returns_restart_requirement(tmp_path):
    settings = replace(
        Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"),
        database_url=f"sqlite:///{(tmp_path / 'updates.db').as_posix()}",
    )
    repository = SqliteMetadataStore(tmp_path / "updates.db")

    class Installer:
        def __init__(self):
            self.calls = 0

        def install(self):
            self.calls += 1

    installer = Installer()
    service = UpdateService(
        GitHubReleaseClient(open_url=lambda *_args, **_kwargs: FakeResponse([release()])), installer,
    )
    app = create_app(
        repository=repository, settings=settings,
        source_adapter=YouTubeSourceAdapter(settings.allowed_source_hosts),
        separator=DemucsSeparator(), update_service=service,
    )

    async def send(method, path, **kwargs):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.request(method, path, **kwargs)

    check = asyncio.run(send("GET", "/api/v1/updates"))
    denied = asyncio.run(send("POST", "/api/v1/updates/apply", json={"tag_name": "v2.1.0", "confirmed": False}))
    applied = asyncio.run(send("POST", "/api/v1/updates/apply", json={"tag_name": "v2.1.0", "confirmed": True}))

    assert check.status_code == 200 and check.json()["releases"][0]["notes"] == "Bug fixes and improvements"
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "confirmation_required"
    assert applied.status_code == 200 and applied.json()["restart_required"] is True
    assert installer.calls == 1


def test_update_api_reports_failed_install_and_can_be_retried(tmp_path):
    settings = replace(
        Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"),
        database_url=f"sqlite:///{(tmp_path / 'update-retry.db').as_posix()}",
    )
    repository = SqliteMetadataStore(tmp_path / "update-retry.db")

    class RetryInstaller:
        calls = 0

        def install(self):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.CalledProcessError(1, "pip")

    installer = RetryInstaller()
    service = UpdateService(
        GitHubReleaseClient(open_url=lambda *_args, **_kwargs: FakeResponse([release()])), installer,
    )
    app = create_app(
        repository=repository, settings=settings,
        source_adapter=YouTubeSourceAdapter(settings.allowed_source_hosts),
        separator=DemucsSeparator(), update_service=service,
    )

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return [
                await client.post("/api/v1/updates/apply", json={"tag_name": "v2.1.0", "confirmed": True}),
                await client.post("/api/v1/updates/apply", json={"tag_name": "v2.1.0", "confirmed": True}),
            ]

    failed, retried = asyncio.run(send())
    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "dependency_update_failed"
    assert retried.status_code == 200 and installer.calls == 2

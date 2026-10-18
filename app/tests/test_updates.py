import json
import subprocess
from pathlib import Path

import pytest
import asyncio
from dataclasses import replace
import httpx
from urllib.parse import quote

from audio_stems.api import create_app
from audio_stems.separator import DemucsSeparator
from audio_stems.db import SqliteMetadataStore
from audio_stems.config import Settings
from audio_stems.models import YouTubeSourceAdapter
from audio_stems.updates import DependencyUpdater, GitHubReleaseClient, UpdateCheckError, UpdateService


PINNED_REQUIREMENTS = "pytubefix==11.2.0\ndemucs==4.1.0\nfastapi==0.139.2\nuvicorn==0.51.0\nnumpy==1.26.4\nimageio-ffmpeg==0.6.0\n"


class FakeResponse:
    def __init__(self, data, url=None):
        self.data = data
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        if isinstance(self.data, bytes):
            return self.data
        if isinstance(self.data, str):
            return self.data.encode("utf-8")
        return json.dumps(self.data).encode("utf-8")

    def geturl(self):
        return self.url


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


def release_client_with_manifest():
    def open_url(request, timeout):
        if request.full_url.startswith("https://api.github.com/"):
            return FakeResponse([release()])
        return FakeResponse(PINNED_REQUIREMENTS, request.full_url)

    return GitHubReleaseClient(open_url=open_url)


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


def test_release_client_fetches_pinned_manifest_from_exact_github_tag():
    calls = []

    def open_url(request, timeout):
        calls.append(request.full_url)
        if request.full_url.startswith("https://api.github.com/"):
            return FakeResponse([release()])
        return FakeResponse(PINNED_REQUIREMENTS, request.full_url)

    client = GitHubReleaseClient(open_url=open_url)
    selected = client.fetch_releases()[0]
    manifest = client.fetch_requirements(selected.tag_name)

    assert manifest == PINNED_REQUIREMENTS
    assert calls[-1] == f"https://raw.githubusercontent.com/sergeypolevich/separate-music-instruments/{quote(selected.tag_name, safe='')}/requirements.txt"


def test_checked_in_runtime_requirements_are_the_exact_release_pins():
    requirements = Path(__file__).parents[1] / "requirements.txt"
    assert requirements.read_text(encoding="utf-8") == PINNED_REQUIREMENTS


def test_update_action_is_unavailable_for_unpinned_release_manifest():
    def open_url(request, timeout):
        if request.full_url.startswith("https://api.github.com/"):
            return FakeResponse([release()])
        return FakeResponse(
            "pytubefix\ndemucs==4.1.0\nfastapi==0.139.2\nuvicorn==0.51.0\nnumpy==1.26.4\n", request.full_url
        )

    client = GitHubReleaseClient(open_url=open_url)
    selected = client.fetch_releases()[0]
    service = UpdateService(client, object())

    assert service.can_update(selected) is False


def test_release_manifest_redirect_is_rejected():
    def open_url(request, timeout):
        if request.full_url.startswith("https://api.github.com/"):
            return FakeResponse([release()])
        return FakeResponse(PINNED_REQUIREMENTS, "https://evil.example/requirements.txt")

    client = GitHubReleaseClient(open_url=open_url)
    with pytest.raises(ValueError, match="redirected"):
        client.fetch_requirements("v2.1.0")


def test_update_requires_explicit_confirmation_before_contacting_github():
    class NeverFetch:
        def fetch_releases(self):
            raise AssertionError("must not inspect releases without confirmation")

        def fetch_requirements(self, _tag):
            raise AssertionError("must not fetch a manifest without confirmation")

    class NeverInstall:
        def install(self, _manifest):
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


def test_update_installs_only_a_pinned_release_manifest(tmp_path):
    root = tmp_path / "application"
    root.mkdir()
    commands = []

    def capture_command(command, **kwargs):
        commands.append((command, kwargs, Path(command[-1]).read_text(encoding="utf-8")))

    updater = DependencyUpdater(
        root, python="python-test",
        runner=capture_command,
    )

    updater.install(PINNED_REQUIREMENTS)

    command, kwargs, manifest_content = commands[0]
    assert command[:6] == ["python-test", "-m", "pip", "install", "--upgrade", "-r"]
    assert manifest_content == PINNED_REQUIREMENTS
    assert kwargs["cwd"] == root
    assert kwargs["check"] is True
    assert kwargs["timeout"] == 1800
    assert not Path(command[5]).exists()


def test_updater_rejects_unpinned_or_external_requirement_lines(tmp_path):
    root = tmp_path / "application"
    root.mkdir()
    updater = DependencyUpdater(root, python="python-test", runner=lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError, match="exact pinned"):
        updater.install(PINNED_REQUIREMENTS.replace("demucs==4.1.0", "demucs>=4.1"))
    with pytest.raises(ValueError, match="exact pinned"):
        updater.install(PINNED_REQUIREMENTS + "--extra-index-url https://packages.example\n")
    with pytest.raises(ValueError, match="only the approved runtime packages"):
        updater.install(PINNED_REQUIREMENTS + "arbitrary-package==9.9\n")


def test_update_only_accepts_latest_stable_release_and_propagates_install_failure():
    class Installer:
        def install(self, _manifest):
            raise subprocess.CalledProcessError(1, "pip")

    class Source:
        def fetch_releases(self):
            return [GitHubReleaseClient(open_url=lambda *_args, **_kwargs: FakeResponse([release()])).fetch_releases()[0]]

        def fetch_requirements(self, _tag):
            return PINNED_REQUIREMENTS

    service = UpdateService(
        Source(), Installer(),
    )

    with pytest.raises(ValueError, match="latest stable"):
        service.apply("v2.0.0", confirmed=True)
    with pytest.raises(subprocess.CalledProcessError):
        service.apply("v2.1.0", confirmed=True)


def test_update_api_checks_release_notes_and_returns_restart_requirement(tmp_path):
    settings = replace(
        Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"),
        database_url=f"sqlite:///{(tmp_path / 'audio_stems.updates.db').as_posix()}",
    )
    repository = SqliteMetadataStore(tmp_path / "audio_stems.updates.db")

    class Installer:
        def __init__(self):
            self.calls = 0

        def install(self, _manifest):
            self.calls += 1

    installer = Installer()
    service = UpdateService(release_client_with_manifest(), installer)
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
    assert check.json()["releases"][0]["updatable"] is True
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

        def install(self, _manifest):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.CalledProcessError(1, "pip")

    installer = RetryInstaller()
    service = UpdateService(release_client_with_manifest(), installer)
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

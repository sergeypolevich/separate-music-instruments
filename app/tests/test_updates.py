import asyncio
from dataclasses import replace
import io
import json
import subprocess
import zipfile

import httpx
import pytest

from audio_stems.api import create_app
from audio_stems.config import Settings
from audio_stems.db import SqliteMetadataStore
from audio_stems.models import SourceKind, SourceRequest, YouTubeSourceAdapter
from audio_stems.separator import StemSeparator
from audio_stems.updates import (
    GitHubReleaseClient, UpdateError, Updater, apply_staged_update, current_version, release_number, unpack_release,
)


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


def release_zip(version="18", extra=()):
    """What GitHub's zipball looks like: one top folder with the repository inside."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        top = "sergeypolevich-separate-music-instruments-abc1234/"
        archive.writestr(top + "Start.bat", "@echo new")
        archive.writestr(top + "app/VERSION", f"{version}\n")
        archive.writestr(top + "app/audio_stems/__init__.py", "")
        archive.writestr(top + "app/audio_stems/api.py", "# new api")
        for name, data in extra:
            archive.writestr(name, data)
    return buffer.getvalue()


def github(releases, archive=b"", archive_url="https://codeload.github.com/x/zip/refs/tags/v18"):
    def open_url(request, timeout):
        if "/zipball/" in request.full_url:
            return FakeResponse(archive, archive_url)
        return FakeResponse(releases)
    return open_url


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


def test_this_copy_knows_its_version_and_release_tags_are_numbers():
    assert current_version() >= 17
    assert [release_number(tag) for tag in ("v17", "18", "v3.0.0", "v18-beta")] == [17, 18, None, None]


def test_check_lists_only_releases_newer_than_this_copy_newest_first(tmp_path):
    open_url = github([release("v16"), release("v19"), release("v17"), release("v18")])
    updater = Updater(tmp_path, tmp_path / "update", open_url=open_url, version=lambda: 17)

    found = updater.check()

    assert found["current"] == 17 and found["mode"] == "archive"
    assert [r.tag_name for r in found["releases"]] == ["v19", "v18"]
    with pytest.raises(UpdateError, match="newest"):
        updater.apply("v18")


def test_a_zip_copy_downloads_the_release_and_the_launcher_installs_it_on_the_next_start(tmp_path):
    root, staging = tmp_path / "Audio Stems", tmp_path / "data" / "update"
    (root / "app" / "audio_stems").mkdir(parents=True)
    (root / "app" / "audio_stems" / "api.py").write_text("# old api")
    (root / "app" / ".venv").mkdir()
    (root / "app" / ".venv" / "python.exe").write_text("the environment")
    (root / "app" / ".env").write_text("AUDIO_STEMS_API_HOST=0.0.0.0")
    updater = Updater(root, staging, open_url=github([release("v18")], release_zip()), version=lambda: 17)

    assert updater.apply("v18") == "staged"
    assert (root / "app" / "audio_stems" / "api.py").read_text() == "# old api"  # nothing replaced while running

    assert apply_staged_update(root, staging) == "v18"
    assert (root / "app" / "audio_stems" / "api.py").read_text() == "# new api"
    assert (root / "Start.bat").read_text() == "@echo new" and (root / "app" / "VERSION").read_text() == "18\n"
    assert (root / "app" / ".venv" / "python.exe").read_text() == "the environment"
    assert (root / "app" / ".env").read_text() == "AUDIO_STEMS_API_HOST=0.0.0.0"
    assert not staging.exists() and apply_staged_update(root, staging) is None


def test_a_bad_release_archive_is_refused_and_leaves_nothing_to_install(tmp_path):
    staging = tmp_path / "update"
    cases = [
        (release_zip(extra=[("top/../../evil.txt", "x")]), "https://codeload.github.com/z", "could not be unpacked|unsafe"),
        (release_zip(version="20"), "https://codeload.github.com/z", "another version"),
        (release_zip(), "https://evil.example/z", "unexpected address"),
        (b"not a zip", "https://codeload.github.com/z", "could not be unpacked"),
    ]
    for archive, url, message in cases:
        updater = Updater(tmp_path, staging, open_url=github([release("v18")], archive, url), version=lambda: 17)
        with pytest.raises(UpdateError, match=message):
            updater.apply("v18")
        assert not (staging / "ready").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_unpacking_refuses_paths_that_leave_the_target(tmp_path):
    with pytest.raises(UpdateError, match="unsafe"):
        unpack_release(release_zip(extra=[("top/../../evil.txt", "x")]), tmp_path / "out")


def test_a_git_clone_fast_forwards_to_the_tag_only_when_clean_and_behind(tmp_path):
    (tmp_path / ".git").mkdir()
    commands = []

    def runner(command, **_kwargs):
        commands.append(command[1:])
        if command[1] == "status":
            return subprocess.CompletedProcess(command, 0, stdout=status[0])
        if command[1] == "merge" and not fast_forward[0]:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout="")

    status, fast_forward = [""], [True]
    updater = Updater(tmp_path, tmp_path / "update", open_url=github([release("v18")]), runner=runner, version=lambda: 17)
    assert updater.check()["mode"] == "git"
    assert updater.apply("v18") == "applied"
    assert commands == [["status", "--porcelain", "--untracked-files=no"], ["fetch", "--tags", "origin"],
                        ["merge", "--ff-only", "refs/tags/v18"]]
    status[0] = " M app/audio_stems/api.py\n"
    with pytest.raises(UpdateError, match="local changes"):
        updater.apply("v18")
    status[0], fast_forward[0] = "", False
    with pytest.raises(UpdateError, match="commits"):
        updater.apply("v18")


def _api(tmp_path, updater):
    settings = replace(Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"),
                       database_url=f"sqlite:///{(tmp_path / 'u.db').as_posix()}")
    repository = SqliteMetadataStore(tmp_path / "u.db")
    app = create_app(repository=repository, settings=settings, source_adapter=YouTubeSourceAdapter(settings.allowed_source_hosts),
                     separator=StemSeparator(), updater=updater)

    async def send(method, path, body=None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            return await client.request(method, path, json=body)

    return (lambda method, path, body=None: asyncio.run(send(method, path, body))), repository


def test_the_update_api_lists_new_releases_and_installs_only_when_confirmed_and_idle(tmp_path):
    updater = Updater(tmp_path / "root", tmp_path / "update", open_url=github([release("v18")], release_zip()), version=lambda: 17)
    request, repository = _api(tmp_path, updater)

    found = request("GET", "/api/v1/updates").json()
    assert found["current"] == 17 and [r["tag_name"] for r in found["releases"]] == ["v18"]
    assert found["releases"][0]["notes"] == "Bug fixes and improvements"
    assert request("POST", "/api/v1/updates/apply", {"tag_name": "v18", "confirmed": False}).json()["error"]["code"] == "confirmation_required"
    repository.create_batch("local", [SourceRequest(SourceKind.SEARCH_QUERY, "a song")])  # a task waits
    busy = request("POST", "/api/v1/updates/apply", {"tag_name": "v18", "confirmed": True})
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "busy"


def test_the_update_api_stages_the_release_when_idle(tmp_path):
    updater = Updater(tmp_path / "root", tmp_path / "update", open_url=github([release("v18")], release_zip()), version=lambda: 17)
    request, _repository = _api(tmp_path, updater)

    applied = request("POST", "/api/v1/updates/apply", {"tag_name": "v18", "confirmed": True})

    assert applied.status_code == 200 and applied.json() == {"state": "staged", "tag_name": "v18", "restart_required": True}
    assert (tmp_path / "update" / "ready").read_text() == "v18"


def test_github_out_of_reach_is_a_stable_error(tmp_path):
    def offline(*_args, **_kwargs):
        raise OSError("offline")

    request, _repository = _api(tmp_path, Updater(tmp_path, tmp_path / "update", open_url=offline, version=lambda: 17))

    assert request("GET", "/api/v1/updates").json()["error"]["code"] == "update_check_failed"

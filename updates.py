"""Fixed-source release inspection and explicitly confirmed dependency updates."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Callable, Protocol
from urllib.request import Request, urlopen


_RELEASES_URL = "https://api.github.com/repos/sergeypolevich/separate-music-instruments/releases?per_page=10"
_RELEASE_PAGE_PREFIX = "https://github.com/sergeypolevich/separate-music-instruments/releases/tag/"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_NOTES_LENGTH = 20_000


@dataclass(frozen=True)
class ReleaseNote:
    tag_name: str
    name: str
    notes: str
    url: str
    published_at: str


class ReleaseSource(Protocol):
    def fetch_releases(self) -> list[ReleaseNote]: ...


class RequirementsInstaller(Protocol):
    def install(self) -> None: ...


class UpdateCheckError(RuntimeError):
    """Release metadata could not be safely loaded."""


class GitHubReleaseClient:
    """Read stable release notes from the one configured public repository."""

    def __init__(self, open_url: Callable = urlopen) -> None:
        self.open_url = open_url

    def fetch_releases(self) -> list[ReleaseNote]:
        request = Request(
            _RELEASES_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Audio-Stems-Updater"},
        )
        with self.open_url(request, timeout=10) as response:
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(payload) > _MAX_RESPONSE_BYTES:
            raise ValueError("GitHub release response exceeded the allowed size")
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("GitHub returned invalid release data") from exc
        if not isinstance(data, list):
            raise ValueError("GitHub release list has an invalid format")

        releases: list[ReleaseNote] = []
        for item in data:
            if not isinstance(item, dict) or item.get("draft") is True or item.get("prerelease") is True:
                continue
            tag = item.get("tag_name")
            url = item.get("html_url")
            if not isinstance(tag, str) or not tag or len(tag) > 64 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-" for char in tag):
                raise ValueError("GitHub release tag has an invalid format")
            if url != _RELEASE_PAGE_PREFIX + tag:
                raise ValueError("GitHub release URL is not from the trusted GitHub project")
            name = item.get("name")
            body = item.get("body")
            published_at = item.get("published_at")
            if not isinstance(name, str) or not isinstance(body, str) or not isinstance(published_at, str):
                raise ValueError("GitHub release metadata is incomplete")
            releases.append(ReleaseNote(tag, name[:200], body[:_MAX_NOTES_LENGTH], url, published_at[:40]))
        return releases


class DependencyUpdater:
    """Run pip only against the app's fixed requirements.txt and interpreter."""

    def __init__(
        self,
        root: Path,
        requirements: Path,
        *,
        python: str | None = None,
        runner: Callable = subprocess.run,
    ) -> None:
        self.root = root.resolve()
        self.requirements = requirements.resolve()
        if not self.requirements.is_relative_to(self.root) or self.requirements.name != "requirements.txt":
            raise ValueError("Updater only accepts the application requirements.txt")
        self.python = python or sys.executable
        self.runner = runner

    def install(self) -> None:
        if not self.requirements.is_file():
            raise FileNotFoundError("Application requirements.txt was not found")
        self.runner(
            [self.python, "-m", "pip", "install", "--upgrade", "-r", str(self.requirements)],
            cwd=self.root,
            check=True,
            timeout=1800,
        )


class UpdateService:
    def __init__(self, releases: ReleaseSource, installer: RequirementsInstaller) -> None:
        self.releases = releases
        self.installer = installer

    def check(self) -> list[ReleaseNote]:
        try:
            return self.releases.fetch_releases()
        except Exception as exc:
            raise UpdateCheckError("Release metadata could not be retrieved") from exc

    def apply(self, tag_name: str, *, confirmed: bool) -> ReleaseNote:
        if confirmed is not True:
            raise PermissionError("Explicit confirmation is required before updating dependencies")
        releases = self.check()
        if not releases or releases[0].tag_name != tag_name:
            raise ValueError("Only the latest stable release can be applied")
        self.installer.install()
        return releases[0]

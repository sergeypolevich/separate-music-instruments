"""Fixed-source release inspection and explicitly confirmed dependency updates."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import re
from typing import Callable
from urllib.parse import quote
from urllib.request import Request, urlopen


_RELEASES_URL = "https://api.github.com/repos/sergeypolevich/separate-music-instruments/releases?per_page=10"
_RELEASE_PAGE_PREFIX = "https://github.com/sergeypolevich/separate-music-instruments/releases/tag/"
_RAW_REQUIREMENTS_PREFIX = "https://raw.githubusercontent.com/sergeypolevich/separate-music-instruments/"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_REQUIREMENTS_BYTES = 64 * 1024
_MAX_NOTES_LENGTH = 20_000
_REQUIRED_RUNTIME_PACKAGES = {"pytubefix", "demucs", "fastapi", "uvicorn", "numpy"}
_ALLOWED_RUNTIME_PACKAGES = _REQUIRED_RUNTIME_PACKAGES | {"imageio-ffmpeg", "soundfile"}
_PINNED_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.+!_-]*)$")


@dataclass(frozen=True)
class ReleaseNote:
    tag_name: str
    name: str
    notes: str
    url: str
    published_at: str


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

    def fetch_requirements(self, tag_name: str) -> str:
        if not tag_name or len(tag_name) > 64 or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-" for char in tag_name
        ):
            raise ValueError("Release tag has an invalid format")
        url = f"{_RAW_REQUIREMENTS_PREFIX}{quote(tag_name, safe='')}/requirements.txt"
        request = Request(url, headers={"User-Agent": "Audio-Stems-Updater"})
        with self.open_url(request, timeout=10) as response:
            payload = response.read(_MAX_REQUIREMENTS_BYTES + 1)
            final_url = response.geturl() if hasattr(response, "geturl") else url
        if final_url != url:
            raise ValueError("Requirements manifest redirected away from its trusted release path")
        if len(payload) > _MAX_REQUIREMENTS_BYTES:
            raise ValueError("Requirements manifest exceeded the allowed size")
        try:
            manifest = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Requirements manifest is not valid UTF-8") from exc
        validate_pinned_requirements(manifest)
        return manifest


class DependencyUpdater:
    """Run pip using a bounded, exactly pinned manifest from a trusted release."""

    def __init__(
        self,
        root: Path,
        *,
        python: str | None = None,
        runner: Callable = subprocess.run,
    ) -> None:
        self.root = root.resolve()
        self.python = python or sys.executable
        self.runner = runner

    def install(self, manifest: str) -> None:
        validate_pinned_requirements(manifest)
        with tempfile.TemporaryDirectory(prefix="audio-stems-update-") as temporary:
            manifest_path = Path(temporary) / "requirements.txt"
            manifest_path.write_text(manifest, encoding="utf-8")
            self.runner(
                [self.python, "-m", "pip", "install", "--upgrade", "-r", str(manifest_path)],
                cwd=self.root,
                check=True,
                timeout=1800,
            )


def validate_pinned_requirements(manifest: str) -> tuple[str, ...]:
    packages: list[str] = []
    for line in manifest.splitlines():
        requirement = line.split("#", 1)[0].strip()
        if not requirement:
            continue
        match = _PINNED_REQUIREMENT.fullmatch(requirement)
        if not match:
            raise ValueError("Release requirements must use exact pinned package versions")
        packages.append(match.group(1).lower().replace("_", "-"))
    missing = _REQUIRED_RUNTIME_PACKAGES - set(packages)
    extra = set(packages) - _ALLOWED_RUNTIME_PACKAGES
    if missing or extra or len(packages) != len(set(packages)):
        raise ValueError("Release requirements must contain only the approved runtime packages, each pinned once")
    return tuple(packages)


class UpdateService:
    def __init__(self, releases: GitHubReleaseClient, installer: DependencyUpdater) -> None:
        self.releases = releases
        self.installer = installer

    def check(self) -> list[ReleaseNote]:
        try:
            return self.releases.fetch_releases()
        except Exception as exc:
            raise UpdateCheckError("Release metadata could not be retrieved") from exc

    def can_update(self, release: ReleaseNote) -> bool:
        try:
            manifest = self.releases.fetch_requirements(release.tag_name)
            validate_pinned_requirements(manifest)
            return True
        except Exception:
            return False

    def apply(self, tag_name: str, *, confirmed: bool) -> ReleaseNote:
        if confirmed is not True:
            raise PermissionError("Explicit confirmation is required before updating dependencies")
        releases = self.check()
        if not releases or releases[0].tag_name != tag_name:
            raise ValueError("Only the latest stable release can be applied")
        try:
            manifest = self.releases.fetch_requirements(tag_name)
            validate_pinned_requirements(manifest)
        except Exception as exc:
            raise UpdateCheckError("The release has no valid exactly pinned requirements manifest") from exc
        self.installer.install(manifest)
        return releases[0]

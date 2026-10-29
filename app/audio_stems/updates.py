"""Updates from the project's GitHub releases: what is new, and installing the latest one.

A copy downloaded as a ZIP gets the release's archive, unpacked into the data folder and copied over the app by
the launcher on its next start (running code is not replaced). A git clone fast-forwards to the release's tag."""
from __future__ import annotations

from dataclasses import dataclass
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
from typing import Callable
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
import zipfile


_REPOSITORY = "sergeypolevich/separate-music-instruments"
_RELEASES_URL = f"https://api.github.com/repos/{_REPOSITORY}/releases?per_page=10"
_RELEASE_PAGE_PREFIX = f"https://github.com/{_REPOSITORY}/releases/tag/"
_ARCHIVE_URL = f"https://api.github.com/repos/{_REPOSITORY}/zipball/"
_ARCHIVE_HOSTS = {"api.github.com", "codeload.github.com"}  # GitHub answers the zipball URL with a redirect there
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
_MAX_NOTES_LENGTH = 20_000
_TAG = re.compile(r"v?(\d+(?:\.\d+)?)")  # v18 or v18.1; v3.0.0 and v18-beta are not app releases
# Never replaced by an update: the environment, the local settings and git's own folder.
_KEEP = (".git", "app/.venv", "app/.env")
VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION"


def current_version() -> str:
    """This copy's version (app/VERSION, the number of the release it was made for: "18" or "18.1")."""
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def release_number(tag: str) -> tuple[int, ...] | None:
    """A release tag or version as a comparable number: "v18" -> (18,), "18.1" -> (18, 1)."""
    match = _TAG.fullmatch(tag)
    return tuple(int(part) for part in match[1].split(".")) if match else None


class UpdateError(RuntimeError):
    """The update cannot be installed this way (local changes, a release that is not newest, a bad archive)."""


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


class Updater:
    def __init__(self, root: Path, staging: Path, releases: GitHubReleaseClient | None = None, *,
                 open_url: Callable = urlopen, runner: Callable = subprocess.run, version: Callable[[], str] = current_version) -> None:
        self.root = root  # the folder with Start Windows.bat and app/
        self.staging = staging  # in the data folder: never inside the app it replaces
        self.releases = releases or GitHubReleaseClient(open_url)
        self.open_url = open_url
        self.runner = runner
        self.version = version

    @property
    def mode(self) -> str:
        return "git" if (self.root / ".git").exists() else "archive"

    def check(self) -> dict[str, object]:
        """Releases newer than this copy, newest first."""
        try:
            releases = self.releases.fetch_releases()
        except Exception as exc:
            raise UpdateCheckError("Release metadata could not be retrieved") from exc
        current = self.version()
        here = release_number(current) or (0,)
        newer = [r for r in releases if (number := release_number(r.tag_name)) is not None and number > here]
        newer.sort(key=lambda r: release_number(r.tag_name), reverse=True)
        return {"current": current, "mode": self.mode, "releases": newer}

    def apply(self, tag_name: str) -> str:
        """Install the newest release: "applied" (git: done, restart to use it) or "staged" (installed on the
        launcher's next start)."""
        newer = self.check()["releases"]
        if not newer or newer[0].tag_name != tag_name:
            raise UpdateError("Only the newest release can be installed")
        if self.mode == "git":
            self._fast_forward(tag_name)
            return "applied"
        self._stage(tag_name)
        return "staged"

    def _git(self, *arguments: str) -> str:
        return self.runner(["git", *arguments], cwd=self.root, capture_output=True, text=True, check=True, timeout=300).stdout

    def _fast_forward(self, tag: str) -> None:
        if self._git("status", "--porcelain", "--untracked-files=no").strip():
            raise UpdateError("The app folder has local changes: update it by hand")
        self._git("fetch", "--tags", "origin")
        try:
            self._git("merge", "--ff-only", f"refs/tags/{tag}")
        except subprocess.CalledProcessError as exc:
            raise UpdateError("This copy has commits the release does not: update it by hand") from exc

    def _stage(self, tag: str) -> None:
        request = Request(_ARCHIVE_URL + quote(tag, safe=""), headers={"User-Agent": "Audio-Stems-Updater"})
        with self.open_url(request, timeout=60) as response:
            final = response.geturl() if hasattr(response, "geturl") else request.full_url
            if urlparse(final).scheme != "https" or urlparse(final).hostname not in _ARCHIVE_HOSTS:
                raise UpdateError("The release archive came from an unexpected address")
            payload = response.read(_MAX_ARCHIVE_BYTES + 1)
        if len(payload) > _MAX_ARCHIVE_BYTES:
            raise UpdateError("The release archive is too large")
        target = self.staging / tag
        shutil.rmtree(self.staging, ignore_errors=True)
        try:
            unpack_release(payload, target)
            if release_number(tag) != release_number((target / "app" / "VERSION").read_text(encoding="utf-8").strip()):
                raise UpdateError("The release archive is for another version")
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            shutil.rmtree(self.staging, ignore_errors=True)
            raise UpdateError("The release archive could not be unpacked") from exc
        except UpdateError:
            shutil.rmtree(self.staging, ignore_errors=True)
            raise
        (self.staging / "ready").write_text(tag, encoding="utf-8")  # last: a half-unpacked update is never used


def unpack_release(payload: bytes, target: Path) -> None:
    """GitHub's archive holds one top folder; its content goes to target. Paths that would leave it are refused."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if not names:
            raise UpdateError("The release archive is empty")
        for name in names:
            parts = PurePosixPath(name).parts[1:]  # without GitHub's "<owner>-<repo>-<commit>/"
            if not parts or any(part in ("..", "") or ":" in part for part in parts) or PurePosixPath(name).is_absolute():
                raise UpdateError("The release archive has an unsafe path")
            destination = target.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
            mode = (archive.getinfo(name).external_attr >> 16) & 0o777
            if mode & 0o111:  # the Mac launcher and scripts stay executable (copy2 below copies the mode)
                destination.chmod(mode)
    if not (target / "app" / "audio_stems" / "__init__.py").is_file():
        raise UpdateError("The release archive does not contain the app")


def apply_staged_update(root: Path, staging: Path) -> str | None:
    """Run by the launcher before the services start: copy a downloaded release over the app (the environment,
    the local settings and git's folder stay) and remove it. Returns the installed tag, or None."""
    ready = staging / "ready"
    if not ready.is_file():
        return None
    tag = ready.read_text(encoding="utf-8").strip()
    source = staging / tag
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source).as_posix()
        if path.is_dir() or any(relative == keep or relative.startswith(keep + "/") for keep in _KEEP):
            continue
        # Next to app/ only what this copy already has: a copy from the Windows or macOS archive does not get the
        # other system's launcher or the developer files from the repository's archive.
        if "/" not in relative and not (root / relative).exists():
            continue
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    shutil.rmtree(staging, ignore_errors=True)
    return tag

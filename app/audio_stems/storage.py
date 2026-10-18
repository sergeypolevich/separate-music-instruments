"""Windows-safe local media storage: audio at the root, stems flat under Stems/<title>/."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
from threading import RLock
from typing import Sequence
from uuid import UUID, uuid4

from audio_stems.models import (
    AdapterError,
    DownloadedAudio,
    FailureCategory,
    MediaAsset,
    MediaAssetType,
    StemArtifact,
    StorageTarget,
    VideoCandidate,
)


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')
_WHITESPACE = re.compile(r"\s+")
_EXTENSION = re.compile(r"^[A-Za-z0-9]{1,10}$")
_STEM_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_INDEX_LOCK = RLock()


def safe_filename(title: str) -> str:
    """Convert a title to a readable filename safe for Windows."""
    title = _INVALID_FILENAME_CHARS.sub("_", title)
    title = _WHITESPACE.sub(" ", title).strip().rstrip(". ")

    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if title.split(".", 1)[0].upper() in reserved:
        title = "_" + title
    return title or "untitled"


class LocalMediaStorage:
    """Allocate safe local paths and expose relative keys for saved assets."""

    def __init__(
        self,
        root: Path,
        *,
        stems_root: Path | None = None,
        temp_root: Path | None = None,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.stems_root = (stems_root or self.root / "Stems").expanduser().resolve()
        self.temp_root = (temp_root or self.root / "_demucs_temp").expanduser().resolve()
        if not self.stems_root.is_relative_to(self.root):
            raise ValueError("The stems directory must be inside the storage root")
        self._index_path = self.root / ".audio-stems" / "audio_stems.models.json"

    def _read_index(self) -> dict[str, str]:
        if not self._index_path.exists():
            return {}
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AdapterError("Local storage identity index could not be read", FailureCategory.PERMANENT) from exc
        if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in data.items()):
            raise AdapterError("Local storage identity index has an invalid format", FailureCategory.PERMANENT)
        return data

    def _write_index(self, data: dict[str, str]) -> None:
        try:
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._index_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            temporary.replace(self._index_path)
        except OSError as exc:
            raise AdapterError("Local storage identity index could not be saved", FailureCategory.TRANSIENT) from exc

    @staticmethod
    def _source_suffix(source_id: str) -> str:
        safe = safe_filename(source_id)
        if safe != source_id:
            digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:8]
            safe = f"{safe[:24]}-{digest}"
        return safe

    def _claim_title(self, namespace: str, title: str, source_id: str | None) -> str:
        safe_title = safe_filename(title)
        if not source_id:
            return safe_title

        index_key = f"{namespace}:{safe_title.casefold()}"
        identity_key = f"source:{namespace}:{source_id}"
        with _INDEX_LOCK:
            index = self._read_index()
            assigned_title = index.get(identity_key)
            if assigned_title is not None:
                return assigned_title
            known_source = index.get(index_key)
            if known_source is None:
                # Existing files without an index are legacy baseline outputs.
                # Claim the readable name for their first source to preserve reuse.
                index[index_key] = source_id
                assigned_title = safe_title
            elif known_source == source_id:
                assigned_title = safe_title
            else:
                assigned_title = f"{safe_title}--{self._source_suffix(source_id)}"
            index[identity_key] = assigned_title
            self._write_index(index)
            return assigned_title

    def _key_for(self, path: Path) -> str:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise AdapterError("Storage path is outside the configured root", FailureCategory.INVALID_INPUT)
        return resolved.relative_to(self.root).as_posix()

    def resolve_path(self, storage_key: str) -> Path:
        if (
            not storage_key
            or "\\" in storage_key
            or any(ord(char) < 32 or char in '<>:"|?*' for char in storage_key)
        ):
            raise AdapterError("Invalid storage key", FailureCategory.INVALID_INPUT)
        posix_key = PurePosixPath(storage_key)
        windows_key = PureWindowsPath(storage_key)
        if posix_key.is_absolute() or windows_key.is_absolute() or windows_key.drive:
            raise AdapterError("Absolute storage keys are not allowed", FailureCategory.INVALID_INPUT)
        if any(part in {"", ".", ".."} for part in posix_key.parts):
            raise AdapterError("Storage key traversal is not allowed", FailureCategory.INVALID_INPUT)
        target = self.root.joinpath(*posix_key.parts).resolve()
        if not target.is_relative_to(self.root):
            raise AdapterError("Storage key escapes the configured root", FailureCategory.INVALID_INPUT)
        return target

    def audio_target(self, title: str, extension: str, source_id: str | None = None) -> StorageTarget:
        extension = extension.lstrip(".")
        if not _EXTENSION.fullmatch(extension):
            raise AdapterError("Invalid audio extension", FailureCategory.INVALID_INPUT)
        name = self._claim_title("audio", title, source_id)
        path = self.root / f"{name}.{extension}"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AdapterError("Audio storage directory could not be created", FailureCategory.TRANSIENT) from exc
        return StorageTarget(self._key_for(path), path)

    def find_existing_audio(self, source: VideoCandidate) -> DownloadedAudio | None:
        """Reuse an audio file only when its container has a recognizable header."""
        if not self.root.is_dir():
            return None
        index = self._read_index()
        identity_key = f"source:audio:{source.source_id}"
        title = index.get(identity_key)
        if title is None:
            safe_title = safe_filename(source.title)
            known_source = index.get(f"audio:{safe_title.casefold()}")
            title = (
                f"{safe_title}--{self._source_suffix(source.source_id)}"
                if known_source is not None and known_source != source.source_id
                else safe_title
            )
        allowed_extensions = ("webm", "m4a", "opus", "mp3", "ogg", "flac", "aac", "wma", "aiff", "aif")
        try:
            matches = {
                path.suffix[1:].lower(): path
                for path in self.root.iterdir()
                if path.is_file()
                and path.stem.casefold() == title.casefold()
                and path.suffix[1:].lower() in allowed_extensions
                and path.stat().st_size > 0
                and self.validate_audio_file(path, path.suffix[1:].lower())
            }
        except OSError as exc:
            raise AdapterError("Audio storage could not be searched", FailureCategory.TRANSIENT) from exc
        for extension in allowed_extensions:
            path = matches.get(extension)
            if path is not None:
                if index.get(identity_key) is None:
                    self._claim_title("audio", source.title, source.source_id)
                stored_source = VideoCandidate(source.source_id, title, source.url, source.duration_seconds)
                return DownloadedAudio(path, stored_source, extension, path.stat().st_size)
        return None

    @staticmethod
    def validate_audio_file(path: Path, media_format: str) -> bool:
        """Check basic container/frame markers without decoding audio samples."""
        try:
            with path.open("rb") as audio_file:
                header = audio_file.read(16)
            extension = media_format.lower().lstrip(".")
            if extension == "webm":
                return len(header) >= 8 and header.startswith(b"\x1aE\xdf\xa3")
            if extension in {"m4a", "mp4"}:
                return len(header) >= 12 and header[4:8] == b"ftyp"
            if extension == "mp3":
                return header.startswith(b"ID3") or (
                    len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
                )
            if extension in {"ogg", "opus"}:
                return header.startswith(b"OggS")
            if extension == "flac":
                return header.startswith(b"fLaC")
            if extension == "wav":
                return len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WAVE"
            if extension == "aac":
                return len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xF6) == 0xF0
            if extension == "wma":
                return header.startswith(bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c"))
            if extension in {"aiff", "aif"}:
                return len(header) >= 12 and header.startswith(b"FORM") and header[8:12] in {b"AIFF", b"AIFC"}
        except OSError:
            return False
        return False

    def quarantine_audio(self, path: Path) -> Path | None:
        if not path.exists():
            return None
        self._key_for(path)
        recovery_dir = self.temp_root / "_recovery"
        try:
            recovery_dir.mkdir(parents=True, exist_ok=True)
            destination = recovery_dir / f"{path.name}.{uuid4().hex}.quarantined"
            path.replace(destination)
            return destination
        except OSError as exc:
            raise AdapterError("Invalid audio could not be preserved for recovery", FailureCategory.TRANSIENT) from exc

    def stem_directory(self, title: str, source_id: str | None = None) -> Path:
        name = self._claim_title("stems", title, source_id)
        path = (self.stems_root / name).resolve()
        if not path.is_relative_to(self.stems_root):
            raise AdapterError("Stem directory escapes the configured root", FailureCategory.INVALID_INPUT)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AdapterError("Stem directory could not be created", FailureCategory.TRANSIENT) from exc
        return path

    def stem_file_target(self, title: str, stem_name: str, source_id: str | None = None) -> StorageTarget:
        if not _STEM_NAME.fullmatch(stem_name):
            raise AdapterError("Invalid stem name", FailureCategory.INVALID_INPUT)
        path = self.stem_directory(title, source_id) / f"{stem_name}.wav"
        return StorageTarget(self._key_for(path), path)

    def temporary_directory(self, title: str, source_id: str | None = None) -> Path:
        name = self._claim_title("temp", title, source_id)
        return self.temp_root / name

    def temporary_model_directory(self, title: str, model_name: str, source_id: str | None = None) -> Path:
        return self.temporary_directory(title, source_id) / safe_filename(model_name)

    def stems_complete(
        self,
        title: str,
        expected_stems: Sequence[str],
        source_id: str | None = None,
    ) -> bool:
        directory = self.stem_directory(title, source_id)
        return all((directory / f"{stem}.wav").is_file() for stem in expected_stems)

    def remove_temporary_directory(self, title: str, source_id: str | None = None) -> None:
        shutil.rmtree(self.temporary_directory(title, source_id), ignore_errors=True)

    def save_audio(self, job_id: UUID, audio: DownloadedAudio) -> MediaAsset:
        target = self.audio_target(audio.source.title, audio.media_format, audio.source.source_id)
        try:
            target.path.parent.mkdir(parents=True, exist_ok=True)
            if target.path.is_file() and not self.validate_audio_file(target.path, audio.media_format):
                self.quarantine_audio(target.path)
            if not target.path.is_file():
                if audio.path.resolve() != target.path.resolve():
                    shutil.copy2(audio.path, target.path)
            size = target.path.stat().st_size
        except OSError as exc:
            raise AdapterError("Audio asset could not be saved", FailureCategory.TRANSIENT) from exc
        if not self.validate_audio_file(target.path, audio.media_format):
            raise AdapterError("Downloaded audio has an invalid container header", FailureCategory.PERMANENT)
        return MediaAsset(uuid4(), job_id, MediaAssetType.SOURCE_AUDIO, target.storage_key, size, media_format=audio.media_format)

    def save_stem(
        self,
        job_id: UUID,
        track_title: str,
        source_id: str | None,
        stem: StemArtifact,
    ) -> MediaAsset:
        target = self.stem_file_target(track_title, stem.name, source_id)
        try:
            target.path.parent.mkdir(parents=True, exist_ok=True)
            if stem.path.resolve() != target.path.resolve():
                shutil.copy2(stem.path, target.path)
            size = target.path.stat().st_size
        except OSError as exc:
            raise AdapterError("Stem asset could not be saved", FailureCategory.TRANSIENT) from exc
        return MediaAsset(
            uuid4(), job_id, MediaAssetType.STEM, target.storage_key, size, stem_name=stem.name, media_format="wav"
        )

    def delete_asset(self, storage_key: str) -> None:
        path = self.resolve_path(storage_key)
        try:
            if path.is_dir():
                raise AdapterError("Storage key points to a directory", FailureCategory.INVALID_INPUT)
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise AdapterError("Media asset could not be deleted", FailureCategory.TRANSIENT) from exc

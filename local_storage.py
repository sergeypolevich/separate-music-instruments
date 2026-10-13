"""Windows-safe local media storage behind the MediaStorage contract."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
from threading import RLock
from typing import Sequence
from uuid import UUID, uuid4

from contracts import (
    AdapterError,
    DownloadedAudio,
    FailureCategory,
    MediaAsset,
    MediaAssetType,
    StemArtifact,
    StorageTarget,
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
        self._index_path = self.root / ".audio-stems" / "sources.json"

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
        with _INDEX_LOCK:
            index = self._read_index()
            known_source = index.get(index_key)
            if known_source is None:
                # Existing files without an index are legacy baseline outputs.
                # Claim the readable name for their first source to preserve reuse.
                index[index_key] = source_id
                self._write_index(index)
                return safe_title
            if known_source == source_id:
                return safe_title
            return f"{safe_title}--{self._source_suffix(source_id)}"

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

    def temporary_directory(self, title: str) -> Path:
        return self.temp_root / safe_filename(title)

    def temporary_model_directory(self, title: str, model_name: str) -> Path:
        return self.temporary_directory(title) / safe_filename(model_name)

    def stems_complete(
        self,
        title: str,
        expected_stems: Sequence[str],
        source_id: str | None = None,
    ) -> bool:
        directory = self.stem_directory(title, source_id)
        return all((directory / f"{stem}.wav").is_file() for stem in expected_stems)

    def remove_temporary_directory(self, title: str) -> None:
        shutil.rmtree(self.temporary_directory(title), ignore_errors=True)

    def save_audio(self, job_id: UUID, audio: DownloadedAudio) -> MediaAsset:
        target = self.audio_target(audio.source.title, audio.media_format, audio.source.source_id)
        try:
            target.path.parent.mkdir(parents=True, exist_ok=True)
            if not target.path.is_file() or target.path.stat().st_size == 0:
                if audio.path.resolve() != target.path.resolve():
                    shutil.copy2(audio.path, target.path)
            size = target.path.stat().st_size
        except OSError as exc:
            raise AdapterError("Audio asset could not be saved", FailureCategory.TRANSIENT) from exc
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

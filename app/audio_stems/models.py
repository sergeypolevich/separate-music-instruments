"""Typed data models shared by the modules, and the YouTube input normalizer."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Mapping
from uuid import UUID
from urllib.parse import parse_qs, urlparse


class FailureCategory(Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"


class AdapterError(Exception):
    """Expected, categorized failure raised by an adapter boundary."""

    def __init__(self, message: str, category: FailureCategory) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = category is FailureCategory.TRANSIENT


class SourceKind(Enum):
    SEARCH_QUERY = "search_query"
    VIDEO_URL = "video_url"


@dataclass(frozen=True)
class SourceRequest:
    kind: SourceKind
    value: str


@dataclass(frozen=True)
class VideoCandidate:
    source_id: str
    title: str
    url: str
    duration_seconds: float | None = None


@dataclass(frozen=True)
class DownloadedAudio:
    path: Path
    source: VideoCandidate
    media_format: str
    byte_size: int


@dataclass(frozen=True)
class SeparationOptions:
    model_name: str
    device: str


@dataclass(frozen=True)
class StemArtifact:
    name: str
    path: Path


@dataclass(frozen=True)
class SeparationResult:
    stems: tuple[StemArtifact, ...]


@dataclass(frozen=True)
class SeparatorModel:
    name: str
    stems: tuple[str, ...]


class MediaAssetType(Enum):
    SOURCE_AUDIO = "source_audio"
    STEM = "stem"


@dataclass(frozen=True)
class StorageTarget:
    storage_key: str
    path: Path


@dataclass(frozen=True)
class MediaAsset:
    asset_id: UUID
    job_id: UUID
    asset_type: MediaAssetType
    storage_key: str
    byte_size: int
    stem_name: str | None = None
    media_format: str | None = None
    checksum: str | None = None


class JobStatus(Enum):
    QUEUED = "queued"
    SEARCHING = "searching"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    SEPARATING = "separating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BatchStatus(Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class BatchReceipt:
    batch_id: UUID
    job_ids: tuple[UUID, ...]
    owner_id: str | None = None
    separation_options: SeparationOptions | None = None


@dataclass(frozen=True)
class BatchView:
    batch_id: UUID
    owner_id: str
    submission_method: str
    status: str
    result_summary: str | None
    created_at: datetime


@dataclass(frozen=True)
class QueuedBatch:
    receipt: BatchReceipt
    sources: tuple[SourceRequest, ...]


@dataclass(frozen=True)
class JobView:
    job_id: UUID
    batch_id: UUID
    owner_id: str
    status: JobStatus
    current_stage: str | None = None
    error_summary: str | None = None
    retry_count: int = 0
    separation_options: SeparationOptions | None = None
    input_value: str | None = None


@dataclass(frozen=True)
class JobEvent:
    event_id: UUID
    job_id: UUID
    name: str
    occurred_at: datetime
    details: Mapping[str, str | int | float | bool | None] = field(default_factory=dict)


class YouTubeSourceAdapter:
    def __init__(self, allowed_hosts: tuple[str, ...] | list[str]) -> None:
        self.allowed_hosts = frozenset(host.lower().rstrip(".") for host in allowed_hosts)
        if not self.allowed_hosts:
            raise ValueError("At least one allowed source host is required")

    def normalize(self, value: str) -> SourceRequest:
        candidate = value.strip()
        if not candidate:
            raise AdapterError("Input cannot be empty", FailureCategory.INVALID_INPUT)

        try:
            parsed = urlparse(candidate)
        except ValueError as exc:
            raise AdapterError("Malformed video URL", FailureCategory.INVALID_INPUT) from exc
        first_part = candidate.split("/", 1)[0].split("?", 1)[0].lower().rstrip(".")
        bare_host_like = "." in first_part and " " not in first_part
        url_like = (
            bool(parsed.scheme)
            or candidate.startswith("//")
            or first_part in self.allowed_hosts
            or first_part.startswith("www.")
            or bare_host_like
        )
        if not url_like:
            return SourceRequest(SourceKind.SEARCH_QUERY, candidate)

        if parsed.scheme.lower() != "https" or parsed.username is not None or parsed.password is not None:
            raise AdapterError("Only credential-free HTTPS video URLs are supported", FailureCategory.INVALID_INPUT)
        try:
            if parsed.port is not None:
                raise AdapterError("Video URLs with custom ports are not supported", FailureCategory.INVALID_INPUT)
        except ValueError as exc:
            raise AdapterError("Invalid video URL authority", FailureCategory.INVALID_INPUT) from exc

        host = (parsed.hostname or "").lower().rstrip(".")
        if host not in self.allowed_hosts:
            raise AdapterError("Video URL host is not allowed", FailureCategory.INVALID_INPUT)

        path_parts = [part for part in parsed.path.split("/") if part]
        is_short_host = host in {"youtu.be", "www.youtu.be"}
        if is_short_host:
            has_video_id = len(path_parts) == 1
        elif parsed.path.rstrip("/").lower() == "/watch":
            video_ids = parse_qs(parsed.query).get("v", [])
            has_video_id = len(video_ids) == 1 and bool(video_ids[0])
        else:
            has_video_id = (
                len(path_parts) == 2
                and path_parts[0].lower() in {"shorts", "embed", "live", "v"}
            )

        if not has_video_id:
            raise AdapterError("URL does not identify a supported video", FailureCategory.INVALID_INPUT)
        return SourceRequest(SourceKind.VIDEO_URL, candidate)

"""Provider-independent contracts, typed data models, and the YouTube input normalizer."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Mapping, Protocol, Sequence, runtime_checkable
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
    parameters: Mapping[str, str | int | float | bool] = field(default_factory=dict)


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
    parameters: Mapping[str, str]


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


@runtime_checkable
class SourceAdapter(Protocol):
    """Normalize client text without fetching media.

    Raise ``AdapterError(INVALID_INPUT)`` when the request is unsupported.
    """

    def normalize(self, value: str) -> SourceRequest: ...


@runtime_checkable
class Downloader(Protocol):
    """Search and download source media.

    Implementations use ``NOT_FOUND`` for empty searches and ``TRANSIENT``
    for retryable network/service failures; input and permanent failures are
    categorized separately.
    """

    def search(self, query: str) -> Sequence[VideoCandidate]: ...

    def download(self, source: VideoCandidate) -> DownloadedAudio: ...


@runtime_checkable
class StemSeparator(Protocol):
    """Describe available models and run one with categorized failures."""

    def available_models(self) -> Sequence[SeparatorModel]: ...

    def resolve_device(self, configured: str) -> str:
        """Map the configured device ("auto" = best available) to a concrete one."""
        ...

    def validate_options(self, options: SeparationOptions) -> None: ...

    def separate(
        self,
        audio_path: Path,
        output_dir: Path,
        options: SeparationOptions,
    ) -> SeparationResult: ...


@runtime_checkable
class MediaStorage(Protocol):
    """Manage media assets and return opaque relative keys, never client paths.

    Reject invalid/traversal keys as ``INVALID_INPUT`` and classify filesystem
    failures as retryable or permanent ``AdapterError`` instances.
    """

    def audio_target(self, title: str, extension: str, source_id: str | None = None) -> StorageTarget: ...

    def find_existing_audio(self, source: VideoCandidate) -> DownloadedAudio | None: ...

    def validate_audio_file(self, path: Path, media_format: str) -> bool: ...

    def quarantine_audio(self, path: Path) -> Path | None: ...

    def stem_directory(self, title: str, source_id: str | None = None) -> Path: ...

    def stem_file_target(self, title: str, stem_name: str, source_id: str | None = None) -> StorageTarget: ...

    def temporary_directory(self, title: str, source_id: str | None = None) -> Path: ...

    def temporary_model_directory(self, title: str, model_name: str, source_id: str | None = None) -> Path: ...

    def stems_complete(self, title: str, expected_stems: Sequence[str], source_id: str | None = None) -> bool: ...

    def remove_temporary_directory(self, title: str, source_id: str | None = None) -> None: ...

    def save_audio(self, job_id: UUID, audio: DownloadedAudio) -> MediaAsset: ...

    def save_stem(
        self,
        job_id: UUID,
        track_title: str,
        source_id: str | None,
        stem: StemArtifact,
    ) -> MediaAsset: ...

    def resolve_path(self, storage_key: str) -> Path: ...

    def delete_asset(self, storage_key: str) -> None: ...


@runtime_checkable
class JobRepository(Protocol):
    """Persist jobs/events; raise ``CONFLICT`` for invalid state/idempotency conflicts."""

    def create_batch(
        self,
        owner_id: str,
        sources: Sequence[SourceRequest],
        idempotency_key: str | None = None,
        separation_options: SeparationOptions | None = None,
    ) -> BatchReceipt: ...

    def claim_next_batch(self) -> QueuedBatch | None: ...

    def recover_interrupted_batches(self) -> int: ...

    def list_jobs_for_batch(self, batch_id: UUID, owner_id: str) -> Sequence[JobView]: ...

    def update_project_source(self, job_id: UUID, source_id: str, title: str) -> None: ...

    def get_asset(self, asset_id: UUID, owner_id: str) -> MediaAsset | None: ...

    def transition_job(
        self,
        job_id: UUID,
        status: JobStatus,
        *,
        stage: str | None = None,
        error_summary: str | None = None,
    ) -> JobView: ...

    def transition_batch(self, batch_id: UUID, status: BatchStatus, result_summary: str | None = None) -> None: ...

    def record_retry(self, job_id: UUID, attempt: int) -> None: ...

    def add_asset(self, asset: MediaAsset) -> None: ...

    def get_job(self, job_id: UUID, owner_id: str) -> JobView | None: ...

    def list_events(self, job_id: UUID, owner_id: str) -> Sequence[JobEvent]: ...


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

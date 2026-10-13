"""Normalize search text and allowlisted YouTube video links."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from contracts import AdapterError, FailureCategory, SourceKind, SourceRequest


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

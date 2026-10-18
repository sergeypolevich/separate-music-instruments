"""pytubefix implementation of the provider-independent downloader contract."""
from __future__ import annotations

import hashlib
import logging
from urllib.parse import parse_qs, urlparse

from pytubefix import Search, YouTube

from audio_stems.models import (
    AdapterError,
    DownloadedAudio,
    Downloader,
    FailureCategory,
    VideoCandidate,
)
from audio_stems.storage import LocalMediaStorage
from audio_stems.config import Settings


logger = logging.getLogger("audio_stems.pytube")


def _video_id(url: str) -> str:
    parsed = urlparse(url)
    found = parse_qs(parsed.query).get("v", [None])[0]
    if found:
        return found
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.hostname and parsed.hostname.lower() == "youtu.be" and parts:
        return parts[0]
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


class PytubeDownloader(Downloader):
    def __init__(self, storage: LocalMediaStorage, settings: Settings) -> None:
        self.storage = storage
        self.settings = settings

    def search(self, query: str) -> list[VideoCandidate]:
        try:
            videos = Search(query).videos
            return [
                VideoCandidate(
                    str(getattr(video, "video_id", None) or _video_id(video.watch_url)),
                    video.title,
                    video.watch_url,
                )
                for video in videos
            ]
        except Exception as exc:
            raise AdapterError("Video search failed", FailureCategory.TRANSIENT) from exc

    def download(self, source: VideoCandidate) -> DownloadedAudio:
        try:
            # Use pytubefix's default client and fallback chain. Pinning WEB can
            # require a generated PoToken and brittle player-JS decryption.
            video = YouTube(source.url)
            stream = (
                video.streams.filter(only_audio=True, mime_type="audio/webm")
                .order_by("abr").desc().first()
            )
            if stream is None:
                stream = video.streams.get_audio_only()
            if stream is None:
                raise AdapterError("Video has no audio-only stream", FailureCategory.PERMANENT)

            video_id = str(getattr(video, "video_id", None) or source.source_id)
            selected = VideoCandidate(video_id, video.title, source.url)
            destination = self.storage.audio_target(video.title, stream.subtype, video_id).path
            if destination.is_file():
                if self.storage.validate_audio_file(destination, stream.subtype):
                    return DownloadedAudio(destination, selected, stream.subtype, destination.stat().st_size)
                self.storage.quarantine_audio(destination)

            stream.download(output_path=str(destination.parent), filename=destination.name)
            if not self.storage.validate_audio_file(destination, stream.subtype):
                if destination.exists():
                    self.storage.quarantine_audio(destination)
                raise AdapterError("Downloaded audio failed container validation", FailureCategory.PERMANENT)
            return DownloadedAudio(destination, selected, stream.subtype, destination.stat().st_size)
        except AdapterError:
            raise
        except Exception as exc:
            logger.exception("pytubefix download failed", extra={"event": "audio_download_failed"})
            raise AdapterError("Video audio download failed", FailureCategory.TRANSIENT) from exc

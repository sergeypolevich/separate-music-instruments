from pathlib import Path
import shutil
import subprocess
import sys
import time
import logging
import hashlib
from urllib.parse import parse_qs, urlparse

from pytubefix import Search, YouTube

from logging_setup import configure_logging
from local_storage import LocalMediaStorage, safe_filename
from settings import Settings
from contracts import SourceKind
from sources import YouTubeSourceAdapter


SETTINGS = Settings.load()
logger = logging.getLogger("audio_stems")
SOURCE_ADAPTER = YouTubeSourceAdapter(SETTINGS.allowed_source_hosts)

TRACKS = [
    "Dido - white flag",
]

OUTPUT_DIR = SETTINGS.storage_root
STEMS_DIR = OUTPUT_DIR / "Stems"
TEMP_DIR = SETTINGS.temp_root

STEM_NAMES = [
    "vocals",
    "drums",
    "bass",
    "guitar",
    "piano",
    "other",
]


def download_track(value: str) -> Path | None:
    """Search for the first matching video and download its audio."""
    try:
        source_request = SOURCE_ADAPTER.normalize(value)
        if source_request.kind is SourceKind.SEARCH_QUERY:
            print(f"\nSearching: {source_request.value}")
            logger.info("Searching for source", extra={"event": "source_search_started"})
            results = Search(source_request.value).videos
            if not results:
                print("No videos found.")
                logger.warning("No source candidates found", extra={"event": "source_resolution_failed"})
                return None
            video_url = results[0].watch_url
        else:
            video_url = source_request.value

        yt = YouTube(video_url, "WEB")
        logger.info("Source candidate selected", extra={"event": "source_resolved"})

        print(f"Title: {yt.title}")
        print(f"URL: {yt.watch_url}")

        # Prefer the WebM/Opus audio stream with the highest bitrate.
        streams = (
            yt.streams
            .filter(only_audio=True, mime_type="audio/webm")
            .order_by("abr")
            .desc()
        )

        stream = streams.first()

        # Fall back to any available audio-only stream.
        if stream is None:
            stream = yt.streams.get_audio_only()

        if stream is None:
            print("No audio stream found.")
            logger.warning("Selected source has no audio stream", extra={"event": "audio_download_failed"})
            return None

        source_id = _source_id(yt)
        destination = _local_storage().audio_target(yt.title, stream.subtype, source_id).path

        if destination.exists() and destination.stat().st_size > 0:
            print(f"File already exists: {destination}")
            logger.info("Existing audio file reused", extra={"event": "audio_download_reused"})
            return destination

        # Retry after temporary failures.
        for attempt in range(1, SETTINGS.retry_count + 1):
            try:
                print(
                    f"Downloading, attempt {attempt}/{SETTINGS.retry_count}; "
                    f"format: {stream.subtype}; "
                    f"bitrate: {stream.abr}"
                )
                logger.info(
                    "Audio download attempt started",
                    extra={"event": "audio_download_started", "attempt": attempt},
                )

                stream.download(
                    output_path=str(destination.parent),
                    filename=destination.name,
                )

                if destination.exists() and destination.stat().st_size > 0:
                    print(f"Downloaded: {destination}")
                    logger.info("Audio download completed", extra={"event": "audio_download_completed"})
                    return destination

                raise RuntimeError("The output file was not created or is empty.")

            except Exception as exc:
                print(
                    f"Download error: "
                    f"{type(exc).__name__}: {exc}"
                )
                logger.exception(
                    "Audio download attempt failed",
                    extra={"event": "audio_download_failed", "attempt": attempt},
                )

                if attempt < SETTINGS.retry_count and attempt <= len(SETTINGS.retry_delays):
                    time.sleep(SETTINGS.retry_delays[attempt - 1])

        return None

    except Exception as exc:
        print(
            f"Search or stream retrieval error: "
            f"{type(exc).__name__}: {exc}"
        )
        logger.exception("Source search or resolution failed", extra={"event": "source_resolution_failed"})
        return None


def separate_stems(audio_path: Path) -> None:
    """Separate audio into six stems in a single directory."""
    storage = _local_storage()
    track_name = safe_filename(audio_path.stem)
    track_stems_dir = storage.stem_directory(track_name)

    # Skip processing if all stems already exist.
    if storage.stems_complete(track_name, STEM_NAMES):
        print("All stems already exist. Skipping separation.")
        return

    temp_output = storage.temporary_directory(track_name)
    model_output = storage.temporary_model_directory(track_name, SETTINGS.model_name)

    try:
        print(f"\nSeparating stems: {audio_path.name}")
        logger.info("Stem separation started", extra={"event": "separation_started"})

        command = [
            sys.executable,
            "-m", "demucs",
            "-n", SETTINGS.model_name,
            "-d", SETTINGS.model_device,
            "-o", str(temp_output),
            str(audio_path),
        ]

        subprocess.run(command, check=True)

        # Locate the directory containing the Demucs output.
        candidates = list(model_output.glob("*"))

        if not candidates:
            raise FileNotFoundError(
                f"Stem output directory not found: {model_output}"
            )

        demucs_track_dir = candidates[0]
        found_stems = set()

        for stem_file in demucs_track_dir.glob("*.wav"):
            stem_name = stem_file.stem

            if stem_name not in STEM_NAMES:
                continue

            # Save all stem files directly in the track directory.
            destination_file = storage.stem_file_target(track_name, stem_name).path
            shutil.copy2(stem_file, destination_file)

            found_stems.add(stem_name)
            print(f"Saved stem: {destination_file}")

        missing = set(STEM_NAMES) - found_stems

        if missing:
            print(
                "Missing stems: "
                + ", ".join(sorted(missing))
            )
            logger.error("Stem separation output is incomplete", extra={"event": "separation_failed"})
        else:
            print(f"All stems saved to: {track_stems_dir}")
            logger.info("Stem separation completed", extra={"event": "separation_completed"})

    except subprocess.CalledProcessError as exc:
        print(f"Demucs error, exit code: {exc.returncode}")
        logger.exception("Demucs process failed", extra={"event": "separation_failed"})

    except Exception as exc:
        print(
            f"Separation error: "
            f"{type(exc).__name__}: {exc}"
        )
        logger.exception("Stem separation failed", extra={"event": "separation_failed"})

    finally:
        # Remove temporary data only after all stems have been created.
        if storage.stems_complete(track_name, STEM_NAMES):
            storage.remove_temporary_directory(track_name)


def _source_id(video: object) -> str:
    video_id = getattr(video, "video_id", None)
    if video_id:
        return str(video_id)

    url = getattr(video, "watch_url", "")
    parsed = urlparse(url)
    query_id = parse_qs(parsed.query).get("v", [None])[0]
    if query_id:
        return query_id
    if parsed.hostname and parsed.hostname.lower() in {"youtu.be", "www.youtu.be"}:
        short_id = parsed.path.strip("/").split("/", 1)[0]
        if short_id:
            return short_id
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _local_storage() -> LocalMediaStorage:
    return LocalMediaStorage(OUTPUT_DIR, stems_root=STEMS_DIR, temp_root=TEMP_DIR)


def main() -> None:
    logger.info("Batch processing started", extra={"event": "batch_created"})
    print(f"Audio files: {OUTPUT_DIR}")
    print(f"Stems: {STEMS_DIR}")
    print(f"Number of tracks: {len(TRACKS)}")

    # PHASE 1. Download all tracks.
    downloaded_tracks = []

    print("\n" + "=" * 60)
    print("PHASE 1: AUDIO DOWNLOAD")
    print("=" * 60)

    for index, query in enumerate(TRACKS, start=1):
        print(f"\n[{index}/{len(TRACKS)}] {query}")

        audio_path = download_track(query)

        if audio_path is not None:
            downloaded_tracks.append(audio_path)
        else:
            print("Track skipped: audio download failed.")

        time.sleep(2)

    print("\n" + "=" * 60)
    print(
        f"Download completed: "
        f"{len(downloaded_tracks)} of {len(TRACKS)} tracks."
    )
    print("=" * 60)

    # PHASE 2. Separate downloaded tracks into stems.
    print("\n" + "=" * 60)
    print("PHASE 2: STEM SEPARATION")
    print("=" * 60)

    for index, audio_path in enumerate(downloaded_tracks, start=1):
        print(
            f"\n[{index}/{len(downloaded_tracks)}] "
            f"{audio_path.name}"
        )

        separate_stems(audio_path)

    print("\nProcessing completed.")
    logger.info("Batch processing finished", extra={"event": "batch_processing_finished"})


if __name__ == "__main__":
    configure_logging(SETTINGS)
    main()

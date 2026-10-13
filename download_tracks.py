from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from pytubefix import Search, YouTube


TRACKS = [
    "Dido - white flag",
]

OUTPUT_DIR = Path.home() / "Audio Stems" / "YouTube Audio"
STEMS_DIR = OUTPUT_DIR / "Stems"
TEMP_DIR = OUTPUT_DIR / "_demucs_temp"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
STEMS_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

STEM_NAMES = [
    "vocals",
    "drums",
    "bass",
    "guitar",
    "piano",
    "other",
]


def safe_filename(title: str) -> str:
    """Create a safe Windows filename or directory name."""
    title = re.sub(r'[<>:"/\\|?*]', "_", title)
    title = re.sub(r"\s+", " ", title).strip().rstrip(". ")

    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }

    if title.upper() in reserved:
        title = "_" + title

    return title or "untitled"


def download_track(query: str) -> Path | None:
    """Search for the first matching video and download its audio."""
    print(f"\nSearching: {query}")

    try:
        results = Search(query).videos

        if not results:
            print("No videos found.")
            return None

        yt = YouTube(results[0].watch_url, "WEB")

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
            return None

        filename = safe_filename(yt.title) + "." + stream.subtype
        destination = OUTPUT_DIR / filename

        if destination.exists() and destination.stat().st_size > 0:
            print(f"File already exists: {destination}")
            return destination

        # Retry after temporary failures.
        for attempt in range(1, 4):
            try:
                print(
                    f"Downloading, attempt {attempt}/3; "
                    f"format: {stream.subtype}; "
                    f"bitrate: {stream.abr}"
                )

                stream.download(
                    output_path=str(OUTPUT_DIR),
                    filename=filename,
                )

                if destination.exists() and destination.stat().st_size > 0:
                    print(f"Downloaded: {destination}")
                    return destination

                raise RuntimeError("The output file was not created or is empty.")

            except Exception as exc:
                print(
                    f"Download error: "
                    f"{type(exc).__name__}: {exc}"
                )

                if attempt < 3:
                    time.sleep(attempt * 3)

        return None

    except Exception as exc:
        print(
            f"Search or stream retrieval error: "
            f"{type(exc).__name__}: {exc}"
        )
        return None


def separate_stems(audio_path: Path) -> None:
    """Separate audio into six stems in a single directory."""
    track_name = safe_filename(audio_path.stem)
    track_stems_dir = STEMS_DIR / track_name

    # Skip processing if all stems already exist.
    if all(
        (track_stems_dir / f"{stem}.wav").exists()
        for stem in STEM_NAMES
    ):
        print("All stems already exist. Skipping separation.")
        return

    temp_output = TEMP_DIR / track_name
    model_output = temp_output / "htdemucs_6s"

    try:
        print(f"\nSeparating stems: {audio_path.name}")

        command = [
            sys.executable,
            "-m", "demucs",
            "-n", "htdemucs_6s",
            "-d", "cpu",
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
        track_stems_dir.mkdir(parents=True, exist_ok=True)

        found_stems = set()

        for stem_file in demucs_track_dir.glob("*.wav"):
            stem_name = stem_file.stem

            if stem_name not in STEM_NAMES:
                continue

            # Save all stem files directly in the track directory.
            destination_file = track_stems_dir / f"{stem_name}.wav"
            shutil.copy2(stem_file, destination_file)

            found_stems.add(stem_name)
            print(f"Saved stem: {destination_file}")

        missing = set(STEM_NAMES) - found_stems

        if missing:
            print(
                "Missing stems: "
                + ", ".join(sorted(missing))
            )
        else:
            print(f"All stems saved to: {track_stems_dir}")

    except subprocess.CalledProcessError as exc:
        print(f"Demucs error, exit code: {exc.returncode}")

    except Exception as exc:
        print(
            f"Separation error: "
            f"{type(exc).__name__}: {exc}"
        )

    finally:
        # Remove temporary data only after all stems have been created.
        if all(
            (track_stems_dir / f"{stem}.wav").exists()
            for stem in STEM_NAMES
        ):
            shutil.rmtree(temp_output, ignore_errors=True)


def main() -> None:
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


if __name__ == "__main__":
    main()

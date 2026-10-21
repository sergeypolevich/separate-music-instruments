"""Beat analysis in its own process: the API queues a project, this runner works the queue and stores
the result in SQLite. Kept out of the worker so it never waits behind a long separation."""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import imageio_ffmpeg
import numpy as np

from audio_stems.config import Settings, configure_logging
from audio_stems.db import SqliteMetadataStore
from audio_stems.separator import sha256_file


logger = logging.getLogger("audio_stems.analysis")
# beat_this "final0" on a 4-core laptop CPU: ~25 s per 4-minute song plus ~12 s to start the process
# and load torch and the model (DEVELOPMENT_STATUS, version 11).
SECONDS_PER_MINUTE = 6.5
STARTUP_SECONDS = 12.0
BEAT_CHECKPOINT_URL = "https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt"
BEAT_CHECKPOINT_SHA256 = "8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331"


def decode_mono(audio_path: Path, rate: int = 44100) -> np.ndarray:
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(audio_path), "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(command, capture_output=True, check=True).stdout, np.float32)


def beat_checkpoint(download: Callable[[str, str], None] | None = None) -> Path:
    """beat_this "final0", downloaded once and checked against its pinned SHA-256. Given by its name, beat_this
    itself would fetch it unchecked and unpickle it in full; given a local file, it loads with weights_only."""
    import torch

    path = Path(torch.hub.get_dir()) / "checkpoints" / "beat_this-final0.ckpt"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        (download or torch.hub.download_url_to_file)(BEAT_CHECKPOINT_URL, str(path))
    if sha256_file(path) != BEAT_CHECKPOINT_SHA256:
        path.unlink()  # a broken download is fetched again next time
        raise RuntimeError("beat_this weights failed the integrity check")
    return path


def detect_beats(audio_path: Path, start_seconds: float) -> dict[str, object]:
    """Beat and downbeat times in song time (0 = where the music starts) and the median tempo."""
    from beat_this.inference import Audio2Beats  # heavy (torch); imported only in this process

    tracker = Audio2Beats(checkpoint_path=str(beat_checkpoint()), device="cpu", dbn=False)
    beats, downbeats = tracker(decode_mono(audio_path), 44100)
    shift = lambda times: [round(float(t) - start_seconds, 4) for t in times if t >= start_seconds - 0.05]
    beats, downbeats = shift(beats), shift(downbeats)
    bpm = round(60 / float(np.median(np.diff(beats))), 1) if len(beats) > 2 else None
    return {"beats": beats, "downbeats": downbeats, "bpm": bpm}


def run_queue(
    store: SqliteMetadataStore,
    detect: Callable[[Path, float], dict[str, object]] = detect_beats,
    *,
    tick: float = 0.5,
) -> int:
    """Work queued beat analyses one by one until none is left; return how many ran."""
    done = 0
    while (task := store.claim_analysis("beats")) is not None:
        project_id, request = task
        expected = max(5.0, STARTUP_SECONDS + request.get("minutes", 4) * SECONDS_PER_MINUTE)
        finished = threading.Event()

        def report(started=time.monotonic()):
            # beat_this has no progress callback: estimate from elapsed time, never past 95 %.
            while not finished.wait(tick):
                store.set_analysis_progress(project_id, "beats", min(0.95, (time.monotonic() - started) / expected))

        reporter = threading.Thread(target=report, daemon=True)
        reporter.start()
        try:
            result = detect(Path(request["audio_path"]), float(request.get("start_seconds", 0.0)))
        except Exception:
            logger.exception("Beat analysis failed", extra={"event": "beat_analysis_failed", "project_id": project_id})
            finished.set()
            store.fail_analysis(project_id, "beats", "Не удалось определить доли этого трека")
        else:
            finished.set()
            store.finish_analysis(project_id, "beats", result)
        reporter.join()
        done += 1
    return done


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    settings = Settings.load()
    configure_logging(settings)
    run_queue(SqliteMetadataStore(Path(arguments[0])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

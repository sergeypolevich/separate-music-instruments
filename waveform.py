"""Bounded-memory peak envelope extraction for PCM WAV assets."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import wave

import numpy as np


class WaveformError(ValueError):
    pass


def _sample_peak(data: bytes, sample_width: int) -> float:
    max_value = 1 << (sample_width * 8 - 1)
    if sample_width == 1:
        samples = np.frombuffer(data, np.uint8).astype(np.int16) - 128
    elif sample_width == 3:
        raw = np.frombuffer(data, np.uint8).reshape(-1, 3).astype(np.int32)
        samples = raw[:, 0] | raw[:, 1] << 8 | raw[:, 2] << 16
        samples = np.where(samples & 0x800000, samples - (1 << 24), samples)
    else:
        samples = np.frombuffer(data, {2: "<i2", 4: "<i4"}[sample_width])
    # max/min on the native dtype avoids a widened copy; int() keeps -min from overflowing.
    return min(1.0, max(int(samples.max()), -int(samples.min())) / max_value)


def waveform_data(path: Path, levels: int) -> dict[str, object]:
    """Return at most ``levels`` peak points without loading the full WAV."""
    if levels < 1 or levels > 2048:
        raise WaveformError("Waveform level count is outside supported bounds")
    try:
        stat = path.stat()
    except OSError as exc:
        raise WaveformError("WAV file could not be read") from exc
    # Keyed by mtime and size so a rewritten stem is recomputed instead of served stale.
    return _cached_waveform(str(path), stat.st_mtime_ns, stat.st_size, levels)


@lru_cache(maxsize=64)
def _cached_waveform(path: str, _mtime_ns: int, _size: int, levels: int) -> dict[str, object]:
    try:
        with wave.open(path, "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            frame_count = source.getnframes()
            if source.getcomptype() != "NONE":
                raise WaveformError("Compressed WAV is not supported for waveform extraction")
            if not 1 <= channels <= 32 or sample_width not in {1, 2, 3, 4} or sample_rate <= 0:
                raise WaveformError("WAV encoding is not supported for waveform extraction")

            bucket_count = min(levels, frame_count)
            peaks: list[float] = []
            for bucket_index in range(bucket_count):
                frame_start = bucket_index * frame_count // bucket_count
                frame_end = (bucket_index + 1) * frame_count // bucket_count
                frames_left = frame_end - frame_start
                peak = 0.0
                while frames_left:
                    count = min(frames_left, 65536)
                    samples = source.readframes(count)
                    actual_frames = len(samples) // (channels * sample_width)
                    if actual_frames == 0:
                        raise WaveformError("WAV file ended before its declared frame count")
                    peak = max(peak, _sample_peak(samples, sample_width))
                    frames_left -= actual_frames
                peaks.append(peak)
    except WaveformError:
        raise
    except (OSError, wave.Error, EOFError) as exc:
        raise WaveformError("WAV file could not be read") from exc

    return {
        "sample_rate": sample_rate,
        "channels": channels,
        "frame_count": frame_count,
        "duration_seconds": frame_count / sample_rate,
        "levels": peaks,
    }

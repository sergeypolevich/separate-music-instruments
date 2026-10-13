"""Bounded-memory peak envelope extraction for PCM WAV assets."""
from __future__ import annotations

from pathlib import Path
import wave


class WaveformError(ValueError):
    pass


def _sample_peak(data: bytes, sample_width: int) -> float:
    max_value = 1 << (sample_width * 8 - 1)
    peak = 0
    if sample_width == 1:
        for value in data:
            peak = max(peak, abs(value - 128))
    else:
        for offset in range(0, len(data), sample_width):
            value = int.from_bytes(data[offset:offset + sample_width], "little", signed=True)
            peak = max(peak, abs(value))
    return min(1.0, peak / max_value)


def waveform_data(path: Path, levels: int) -> dict[str, object]:
    """Return at most ``levels`` peak points without loading the full WAV."""
    if levels < 1 or levels > 2048:
        raise WaveformError("Waveform level count is outside supported bounds")
    try:
        with wave.open(str(path), "rb") as source:
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

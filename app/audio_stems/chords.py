"""Chords of a song for the chords plugin (version 22), from its stems without vocals and drums.

A chroma (energy per pitch class) per beat, or per half second when the beats are not known, is matched against
the 24 major and minor triads; a Viterbi pass prefers keeping a chord to changing it, so one off frame does not
flicker. Numpy only, a few seconds on a CPU. It is a starting point: the user corrects chords by hand.
"""
from __future__ import annotations

import numpy as np

ALGORITHM = 1
NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
RATE = 22050
_FRAME, _HOP = 8192, 2048
_CHANGE = 0.12  # the price of a chord change, against cosine scores in 0..1
_QUIET = 0.08  # a segment quieter than this share of the song's median is "no chord"


def labels() -> list[str]:
    return [*NAMES, *(f"{name}m" for name in NAMES), ""]  # "" = no chord


def _templates() -> np.ndarray:
    rows = []
    for third in (4, 3):  # major, then minor
        for root in range(12):
            row = np.zeros(12)
            row[[root, (root + third) % 12, (root + 7) % 12]] = (1.0, 0.8, 0.9)
            rows.append(row / np.linalg.norm(row))
    return np.array(rows)


def chroma(signal: np.ndarray, rate: int = RATE) -> tuple[np.ndarray, np.ndarray]:
    """Frame centre times and a 12-bin chroma per frame (pitch classes from C)."""
    if len(signal) < _FRAME:
        signal = np.pad(signal, (0, _FRAME - len(signal)))
    count = 1 + (len(signal) - _FRAME) // _HOP
    frames = np.lib.stride_tricks.sliding_window_view(signal, _FRAME)[::_HOP][:count] * np.hanning(_FRAME)
    spectrum = np.abs(np.fft.rfft(frames, axis=1))
    freqs = np.fft.rfftfreq(_FRAME, 1 / rate)
    band = (freqs >= 55) & (freqs <= 2000)
    classes = (np.round(12 * np.log2(freqs[band] / 440.0)).astype(int) + 9) % 12  # A = 9
    weights = np.zeros((band.sum(), 12))
    weights[np.arange(band.sum()), classes] = 1.0
    values = np.sqrt(spectrum[:, band]) @ weights
    times = (np.arange(count) * _HOP + _FRAME / 2) / rate
    return times, values


def detect(signal: np.ndarray, beats: list[float] | None = None, rate: int = RATE) -> list[dict[str, object]]:
    """[{"start", "end", "chord"}] in the signal's time; spans without a chord are left out."""
    times, values = chroma(np.asarray(signal, np.float32), rate)
    end_time = len(signal) / rate
    beats = [b for b in (beats or []) if 0 <= b < end_time]
    edges = np.array(beats + [end_time]) if len(beats) >= 8 else np.arange(0, end_time + 0.5, 0.5)
    edges = np.unique(np.clip(np.concatenate([[0.0], edges]), 0, end_time))
    if len(edges) < 2:
        return []
    which = np.clip(np.searchsorted(edges, times, side="right") - 1, 0, len(edges) - 2)
    segments = np.zeros((len(edges) - 1, 12))
    np.add.at(segments, which, values)
    energy = np.linalg.norm(segments, axis=1)
    loud = energy > _QUIET * max(np.median(energy[energy > 0]) if (energy > 0).any() else 0, 1e-9)
    scores = np.zeros((len(segments), 25))
    scores[:, :24] = (segments / np.maximum(energy, 1e-9)[:, None]) @ _templates().T
    scores[~loud, :24] = 0
    scores[~loud, 24] = 1.0
    scores[loud, 24] = 0.3
    # Viterbi: the best path of chords with _CHANGE paid at every change.
    best, back = scores[0].copy(), np.zeros((len(segments), 25), dtype=int)
    for i in range(1, len(segments)):
        stay = best
        move = best.max() - _CHANGE
        back[i] = np.where(stay >= move, np.arange(25), best.argmax())
        best = np.maximum(stay, move) + scores[i]
    path = [int(best.argmax())]
    for i in range(len(segments) - 1, 0, -1):
        path.append(int(back[i][path[-1]]))
    path.reverse()
    names, found = labels(), []
    for i, label in enumerate(path):
        if found and found[-1]["chord"] == names[label] and found[-1]["end"] == round(float(edges[i]), 3):
            found[-1]["end"] = round(float(edges[i + 1]), 3)
        else:
            found.append({"start": round(float(edges[i]), 3), "end": round(float(edges[i + 1]), 3), "chord": names[label]})
    return [span for span in found if span["chord"]]

"""Metronome beats from beat_this's frame activations (version 16).

beat_this picks each peak of its beat activation on its own, which gave double clicks (two lines of peaks a
little apart), phase jumps to the other line and 20 ms steps. Here one chain of beats runs through the whole
song instead: a beat scores the activation under it, and changing the interval from one beat to the next costs
ALPHA * log(ratio)^2, so gradual tempo changes are cheap and jumps or extra beats are not worth it. A song that
one grid holds is played on that exact grid (the tempo rounded to an integer when within 0.05 BPM); otherwise
the chain is smoothed. Reference: the SadSvit listening study, research repository, studies 08 and 09.
"""
from __future__ import annotations

import numpy as np

ALGORITHM = 2  # stored with the result; projects analysed before are analysed again on the next request
FPS = 200  # beat_this's 50 fps activation, upsampled
ALPHA = 400.0


def _evidence(beat_logits) -> tuple[np.ndarray, np.ndarray]:
    act = 1 / (1 + np.exp(-np.asarray(beat_logits, float)))
    frames = np.arange(len(act)) / 50
    times = np.arange(int(frames[-1] * FPS) + 1) / FPS
    return times, np.interp(times, frames, act)


def _chain(act: np.ndarray, period_frames: float, spread: float = 0.25, max_step: int = 4) -> np.ndarray:
    """Frames of the beats maximising sum(act at beats) - ALPHA * sum(log(I_k / I_(k-1))^2)."""
    intervals = np.arange(int(period_frames * (1 - spread)), int(np.ceil(period_frames * (1 + spread))) + 1)
    n, m = len(act), len(intervals)
    score = np.full((n, m), -np.inf)  # score[t, i]: best chain whose beat at t follows the previous one by intervals[i]
    back = np.full((n, m), -1, dtype=np.int64)
    log_i = np.log(intervals)
    change = ALPHA * (log_i[:, None] - log_i[None, :]) ** 2  # [new, old]
    change[np.abs(np.arange(m)[:, None] - np.arange(m)[None, :]) > max_step] = np.inf
    for t in range(n):
        prev = t - intervals
        rows = np.where(prev >= 0)[0]
        if not len(rows):
            continue
        cand = np.full((m, m), -np.inf)
        cand[rows] = score[prev[rows]] - change[rows]
        best_old = np.argmax(cand, axis=1)
        best = cand[np.arange(m), best_old]
        opening = np.where(prev >= 0, act[np.clip(prev, 0, None)], -np.inf)  # the previous beat starts the chain
        score[t] = np.where(prev >= 0, np.maximum(best, opening) + act[t], -np.inf)
        back[t] = np.where(opening > best, -2, best_old)
    t = int(np.argmax(score.max(axis=1)))
    i = int(np.argmax(score[t]))
    beats = [t]
    while True:
        step = back[t, i]
        t -= intervals[i]
        beats.append(t)
        if step < 0:
            return np.array(beats[::-1])
        i = int(step)


def _trim(frames: np.ndarray, act: np.ndarray, floor: float = 0.3) -> np.ndarray:
    """Drop the chain's beats before the first and after the last clear one (intros, fade-outs)."""
    clear = np.where(act[frames] >= floor)[0]
    return frames[clear[0]:clear[-1] + 1] if len(clear) else frames


def _steady_grid(beats: np.ndarray, tight: float = 0.025, core: float = 0.6, loose: float = 0.2,
                 share: float = 0.9) -> np.ndarray | None:
    """A grid fitted on the beats within `tight` seconds of it (the main line, at least `core` of them) that keeps
    `share` of all beats within `loose` of a beat's length (the chain may follow beat_this's second line, 72 ms
    away at 150 BPM in SadSvit, for a while); None otherwise."""
    k = np.arange(len(beats))
    period, offset = np.polyfit(k, beats, 1)
    for tolerance in (0.1, 0.05, tight, tight):
        on = np.abs(beats - (offset + k * period)) < tolerance
        if on.sum() < 2:
            return None
        period, offset = np.polyfit(k[on], beats[on], 1)
    residual = np.abs(beats - (offset + k * period))
    if np.mean(residual < tight) < core or np.mean(residual < loose * period) < share:
        return None
    on = residual < tight
    bpm = 60 / period
    if abs(bpm - round(bpm)) < 0.05:
        period = 60 / round(bpm)
        offset = float(np.median(beats[on] - k[on] * period))
    return offset + k * period


def _smooth(beats: np.ndarray, half: int = 4) -> np.ndarray:
    """A straight line through +-half beats around each one: removes the chain's 5 ms steps, keeps tempo changes."""
    k = np.arange(len(beats))
    out = np.empty_like(beats)
    for i in range(len(beats)):
        lo, hi = max(0, i - half), min(len(beats), i + half + 1)
        slope, offset = np.polyfit(k[lo:hi], beats[lo:hi], 1)
        out[i] = slope * i + offset
    return out


def tempo_every_four(beats) -> list[list[float]]:
    """[[time, bpm], ...] from each fourth beat to the one four later, to one decimal."""
    return [[round(float(beats[i]), 3), round(240 / float(beats[i + 4] - beats[i]), 1)]
            for i in range(0, len(beats) - 4, 4)]


def track(beat_logits, peaks) -> tuple[np.ndarray, str]:
    """Beat times (seconds of the file) and «steady» or «live», from beat_this's beat logits and its own peaks
    (used only for the typical interval). Too few peaks: they are returned as they are."""
    peaks = np.asarray(peaks, float)
    if len(peaks) < 8:
        return peaks, "live"
    gaps = np.diff(peaks)
    typical = np.median(gaps)
    period = float(np.median(gaps[(gaps > 0.75 * typical) & (gaps < 1.33 * typical)]))
    times, act = _evidence(beat_logits)
    beats = times[_trim(_chain(act, period * FPS), act)]
    grid = _steady_grid(beats)
    return (grid, "steady") if grid is not None else (_smooth(beats), "live")

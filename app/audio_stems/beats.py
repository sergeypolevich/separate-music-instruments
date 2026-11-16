"""Metronome beats from beat_this's frame activations (version 16).

beat_this picks each peak of its beat activation on its own, which gave double clicks (two lines of peaks a
little apart), phase jumps to the other line and 20 ms steps. Here one chain of beats runs through the whole
song instead: a beat scores the activation under it, and changing the interval from one beat to the next costs
ALPHA * log(ratio)^2, so gradual tempo changes are cheap and jumps or extra beats are not worth it.

Version 23: the chain is cut into a few long runs of one tempo (a new run only where the tempo changes by 4 % or
more for eight bars). A run that a fixed grid holds is played on that exact grid (a whole BPM when as good);
otherwise the smoothed chain follows the band, and the run still shows one tempo. Bar starts take one meter for
the song. Reference: the SadSvit listening study, research repository, studies 08 and 09; version 23 notes in
DEVELOPMENT_STATUS.md.
"""
from __future__ import annotations

import numpy as np

ALGORITHM = 3  # stored with the result; projects analysed before are analysed again on the next request
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


def _sections(chain: np.ndarray, min_len: int = 32, penalty: float = 16.0, merge: float = 0.04,
              step: int = 4) -> list[tuple[int, int]]:
    """Runs of one tempo, [(first, end)] beat indices: the local tempo (16-beat windows) deviates little from each
    run's median, every run costs `penalty`, runs are at least `min_len` beats (eight bars) and neighbours closer
    than `merge` are joined, so a song gets one tempo unless it really changes (Лесник: 160 after the intro 172)."""
    n = len(chain)
    win = 8
    local = 60 * 2 * win / (chain[np.minimum(n - 1, np.arange(n) + win)] - chain[np.maximum(0, np.arange(n) - win)])
    if n < 2 * min_len:
        return [(0, n)]
    cuts = [c for c in range(0, n, step) if c == 0 or min_len <= c <= n - min_len] + [n]
    best = {0: (0.0, [])}
    for j in cuts[1:]:
        options = []
        for i in cuts:
            if i < j and j - i >= min_len and i in best:
                off = np.minimum(np.abs(local[i:j] / np.median(local[i:j]) - 1) * 100, 3.0)  # %, fills clipped
                options.append((best[i][0] + float((off ** 2).sum()) / 16 + penalty, best[i][1] + [(i, j)]))
        if options:
            best[j] = min(options, key=lambda option: option[0])
    runs = [best[n][1][0]]
    for run in best[n][1][1:]:
        last = runs[-1]
        if abs(np.median(local[last[0]:last[1]]) / np.median(local[run[0]:run[1]]) - 1) < merge:
            runs[-1] = (last[0], run[1])
        else:
            runs.append(run)
    return runs


def _grid(act: np.ndarray, first: float, last: float, bpm: float, fit: float = 0.6) -> np.ndarray | None:
    """The fixed grid from `first` to `last` that sits on the most beat activation (tempo within 1.5 % of `bpm`,
    any phase), a whole BPM when that is as good; None when its beats average less than `fit`: the band plays
    freely there and the beats follow it. Fitting the activation, not the chain, keeps a drum machine on one line
    where beat_this has two (СМРТ: 40-100 ms apart, the chain jumped between them)."""
    def best_phase(tempo, step):
        period = 60 / tempo
        k = np.arange(int((last - first + 0.2) / period) + 1)
        phases = np.arange(-0.1, period - 0.1, step)
        frames = np.clip(np.round((first + phases[:, None] + k[None, :] * period) * FPS).astype(int), 0, len(act) - 1)
        scores = act[frames].mean(axis=1)
        return float(scores.max()), first + phases[int(scores.argmax())] + k * period
    score, beats = max((best_phase(tempo, 0.005) + (tempo,) for tempo in np.arange(bpm * 0.985, bpm * 1.015, 0.02)),
                       key=lambda found: found[0])[:2]
    whole = round(60 / float(np.median(np.diff(beats))))
    if abs(60 / float(np.median(np.diff(beats))) - whole) < 0.06:
        whole_score, whole_beats = best_phase(whole, 0.002)
        if whole_score >= score - 0.01:
            score, beats = whole_score, whole_beats
    return beats if score >= fit else None


def _smooth(beats: np.ndarray, half: int = 4) -> np.ndarray:
    """A straight line through +-half beats around each one: removes the chain's 5 ms steps, keeps tempo changes."""
    k = np.arange(len(beats))
    out = np.empty_like(beats)
    for i in range(len(beats)):
        lo, hi = max(0, i - half), min(len(beats), i + half + 1)
        slope, offset = np.polyfit(k[lo:hi], beats[lo:hi], 1)
        out[i] = slope * i + offset
    return out


def track(beat_logits, peaks) -> tuple[np.ndarray, list[list[float]], str]:
    """Beat times (seconds of the file), the tempo shown for each run of one tempo ([[start, bpm]], to one decimal)
    and «steady» (one grid for the whole song) or «live», from beat_this's beat logits and its own peaks (used only
    for the typical interval). Too few peaks: they are returned as they are."""
    peaks = np.asarray(peaks, float)
    if len(peaks) < 8:
        return peaks, [], "live"
    gaps = np.diff(peaks)
    typical = np.median(gaps)
    period = float(np.median(gaps[(gaps > 0.75 * typical) & (gaps < 1.33 * typical)]))
    times, act = _evidence(beat_logits)
    chain = times[_trim(_chain(act, period * FPS), act)]
    found, tempo, grids = [], [], 0
    for first, end in _sections(chain):
        part = chain[first:end]
        bpm = 60 / float(np.median(np.diff(part)))
        grid = _grid(act, part[0], part[-1], bpm) if len(part) > 8 else None
        part = grid if grid is not None else _smooth(part)
        grids += grid is not None
        if found:  # the run before ends on its own grid: no beat closer than half a beat to its last one
            part = part[part > found[-1][-1] + 0.5 * float(np.median(np.diff(part)))]
        if len(part):
            found.append(part)
            tempo.append([float(part[0]), round(60 / float(np.median(np.diff(part))), 1) if len(part) > 1 else round(bpm, 1)])
    beats = np.concatenate(found)
    return beats, tempo, "steady" if grids == len(tempo) == 1 else "live"


def bars(beats, downbeat_logits, switch: float = 4.0) -> list[float]:
    """Bar starts among the beats: one meter (4, or 3 when clearly better) for the song, and the first beat moves
    only where the downbeat activation gains `switch` from it (a bar of two in a 4/4 song is rare; beat_this's own
    downbeats gave Adele nine of them in a row)."""
    beats = np.asarray(beats, float)
    if not len(beats):
        return []
    act = 1 / (1 + np.exp(-np.asarray(downbeat_logits, float)))
    frames = np.clip(np.round(beats * 50).astype(int), 0, len(act) - 1)
    strength = np.array([act[max(0, frame - 2):frame + 3].max() for frame in frames])
    found = []
    for meter in (3, 4):
        score = np.zeros(meter)
        back = np.zeros((len(beats), meter), int)
        for k, value in enumerate(strength):
            jump = score.max() - switch
            back[k] = np.where(score >= jump, np.arange(meter), score.argmax())
            score = np.maximum(score, jump) + np.where(np.arange(meter) == k % meter, value, 0.0)
        phase = int(score.argmax())
        starts = []
        for k in range(len(beats) - 1, -1, -1):
            if k % meter == phase:
                starts.append(float(beats[k]))
            phase = back[k, phase]
        found.append((score.max() * meter / len(beats), starts[::-1]))
    (three, in_three), (four, in_four) = found
    return in_three if three > four + 0.1 else in_four

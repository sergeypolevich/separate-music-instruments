import numpy as np
import pytest

from audio_stems import beats
from audio_stems.analysis import song_result


def logits(peaks, seconds, strength=None):
    """beat_this-like logits at 50 fps: a 20 ms wide bump at each peak."""
    t = np.arange(int(seconds * 50)) / 50
    act = np.full(t.shape, 0.01)
    for i, peak in enumerate(peaks):
        act = np.maximum(act, (strength[i] if strength is not None else 0.95) * np.exp(-0.5 * ((t - peak) / 0.02) ** 2))
    act = np.clip(act, 1e-4, 1 - 1e-4)
    return np.log(act / (1 - act))


def test_a_steady_song_with_a_second_line_and_double_peaks_gets_one_exact_grid():
    truth = 0.1 + 0.4 * np.arange(150)  # 150 BPM for a minute
    seen = [b + 0.072 if 20 <= b < 30 else b for b in truth]  # beat_this on the other line for 10 s
    seen += [b + 0.08 for b in truth if 35 <= b < 40]  # and both lines for 5 s
    seen = np.sort(seen)

    found, tempo, mode = beats.track(logits(seen, 61), seen)

    assert mode == "steady"
    assert np.allclose(np.diff(found), 0.4)  # exactly 150 BPM: rounded, no 20 ms steps
    assert np.max(np.abs(found - truth[:len(found)])) < 0.004 and len(found) >= len(truth) - 1
    assert song_result(found, [], mode, 0.0, tempo)["tempo"] == [[round(float(found[0]), 3), 150.0]]


def test_a_drum_machine_whose_peaks_jump_between_two_lines_stays_on_one_grid():
    # СМРТ: beat_this's peaks move 40-100 ms off the beat and back every few bars; the tempo is 150 throughout.
    truth = 0.2 + 0.4 * np.arange(300)
    seen = np.array([b + (0.06 if (i // 24) % 3 == 1 else 0.0) for i, b in enumerate(truth)])
    found, tempo, mode = beats.track(logits(seen, truth[-1] + 1, [0.6 if (i // 24) % 3 == 1 else 0.95 for i in range(300)]), seen)
    assert mode == "steady" and [bpm for _, bpm in tempo] == [150.0]
    assert np.allclose(np.diff(found), 0.4) and np.max(np.abs(found - truth[:len(found)])) < 0.004


def test_a_tempo_change_after_the_intro_is_one_step():
    # Лесник: 160 BPM for the intro, then 172 to the end.
    intro = 0.3 + (60 / 160) * np.arange(48)
    truth = np.concatenate([intro, intro[-1] + (60 / 172) * np.arange(1, 240)])
    found, tempo, mode = beats.track(logits(truth, truth[-1] + 1), truth)
    assert mode == "live" and [bpm for _, bpm in tempo] == [160.0, 172.0]
    assert abs(tempo[1][0] - intro[-1]) < 1.5  # the second tempo starts where the song speeds up
    assert np.max(np.abs(found - truth[:len(found)])) < 0.01 and abs(len(found) - len(truth)) <= 1


def test_a_band_that_drifts_a_little_shows_one_tempo_and_is_followed():
    # Борзов: a drummer between 125 and 129 BPM; the beats follow him, the tempo shown does not move.
    period = 60 / (127 + 2 * np.sin(np.arange(400) / 40))
    truth = 0.5 + np.concatenate([[0], np.cumsum(period[:-1])])
    found, tempo, mode = beats.track(logits(truth, truth[-1] + 1), truth)
    assert mode == "live" and len(tempo) == 1 and 125 < tempo[0][1] < 129
    assert np.max(np.abs(found - truth[:len(found)])) < 0.015


def test_a_song_that_speeds_up_is_followed_without_extra_beats():
    period = np.linspace(0.5, 60 / 132, 120)  # 120 -> 132 BPM
    truth = 0.2 + np.concatenate([[0], np.cumsum(period[:-1])])

    found, tempo, mode = beats.track(logits(truth, truth[-1] + 1), truth)

    assert mode == "live"
    assert np.min(np.diff(found)) > 0.4 and abs(len(found) - len(truth)) <= 1
    assert np.max(np.abs(found - truth[:len(found)])) < 0.015


def test_a_steady_tempo_far_from_an_integer_is_kept():
    truth = 0.3 + (60 / 120.6) * np.arange(100)
    found, tempo, mode = beats.track(logits(truth, truth[-1] + 1), truth)
    assert mode == "steady" and [bpm for _, bpm in tempo] == [120.6]


def downbeat_logits(times, seconds):
    return logits(times, seconds, [0.9] * len(times))


def test_bar_starts_keep_one_meter_when_beat_thats_downbeats_slip_for_a_few_bars():
    # Adele: 4/4 throughout, but beat_this marks the third beat too for nine bars.
    found = 0.5 + (60 / 108) * np.arange(200)
    marked = [b for k, b in enumerate(found) if k % 4 == 0 or (60 <= k < 96 and k % 4 == 2)]
    assert beats.bars(found, downbeat_logits(marked, found[-1] + 1)) == [float(b) for b in found[::4]]


def test_bar_starts_follow_a_lasting_shift_and_find_three_four():
    found = 0.5 + 0.5 * np.arange(160)
    shifted = [b for k, b in enumerate(found) if (k <= 80 and k % 4 == 0) or (k >= 82 and k % 4 == 2)]  # a bar of 2
    starts = beats.bars(found, downbeat_logits(shifted, found[-1] + 1))
    assert starts == [float(b) for k, b in enumerate(found) if (k <= 80 and k % 4 == 0) or (k >= 82 and k % 4 == 2)]
    waltz = [float(b) for b in found[1::3]]
    assert beats.bars(found, downbeat_logits(waltz, found[-1] + 1)) == waltz


def test_the_stored_result_drops_beats_before_the_music_and_names_its_algorithm():
    result = song_result(np.array([0.9, 1.4, 1.9, 2.4]), [0.9, 1.4], "steady", 1.0, [[0.9, 120.0]])
    assert result["beats"] == [0.4, 0.9, 1.4]  # -0.1 is before the music
    assert result["downbeats"] == [0.4] and result["algorithm"] == beats.ALGORITHM and result["mode"] == "steady"
    assert result["tempo"] == [[0.4, 120.0]] and result["bpm"] == 120.0


def test_the_song_tempo_is_the_longest_runs():
    found = np.arange(100) * 0.4
    result = song_result(found, [], "live", 0.0, [[0.0, 160.0], [10.0, 172.0]])
    assert result["tempo"] == [[0.0, 160.0], [10.0, 172.0]] and result["bpm"] == 172.0

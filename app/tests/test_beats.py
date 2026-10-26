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

    found, mode = beats.track(logits(seen, 61), seen)

    assert mode == "steady"
    assert np.allclose(np.diff(found), 0.4)  # exactly 150 BPM: rounded, no 20 ms steps
    assert np.max(np.abs(found - truth[:len(found)])) < 0.004 and len(found) >= len(truth) - 1
    assert song_result(found, [], mode, 0.0)["tempo"] == [[round(float(found[0]), 3), 150.0]]


def test_a_song_that_speeds_up_is_followed_without_extra_beats():
    period = np.linspace(0.5, 60 / 132, 120)  # 120 -> 132 BPM
    truth = 0.2 + np.concatenate([[0], np.cumsum(period[:-1])])

    found, mode = beats.track(logits(truth, truth[-1] + 1), truth)
    tempo = beats.tempo_every_four(found)

    assert mode == "live"
    assert np.min(np.diff(found)) > 0.4 and abs(len(found) - len(truth)) <= 1
    assert tempo[0][1] < 122 and tempo[-1][1] > 130  # follows the song
    assert np.max(np.abs(found - truth[:len(found)])) < 0.015


def test_a_steady_tempo_far_from_an_integer_is_kept():
    truth = 0.3 + (60 / 120.6) * np.arange(100)
    found, mode = beats.track(logits(truth, truth[-1] + 1), truth)
    assert mode == "steady" and round(60 / float(np.median(np.diff(found))), 1) == 120.6


@pytest.mark.parametrize(("seconds", "expected"), [(4 * 60 / 151.1234, 151.1), (4 * 60 / 150.0123, 150.0)])
def test_the_tempo_is_kept_every_four_beats_to_one_decimal(seconds, expected):
    found = np.arange(9) * seconds / 4
    assert [bpm for _, bpm in beats.tempo_every_four(found)] == [expected, expected]


def test_the_stored_result_drops_beats_before_the_music_and_names_its_algorithm():
    result = song_result(np.array([0.9, 1.4, 1.9, 2.4]), [1.41], "steady", 1.0)
    assert result["beats"] == [0.4, 0.9, 1.4]  # -0.1 is before the music
    assert result["downbeats"] == [0.4] and result["algorithm"] == beats.ALGORITHM and result["mode"] == "steady"

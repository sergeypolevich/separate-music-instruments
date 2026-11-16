// The plugins' own logic (version 22), without a browser: zoom, beat grid, trim, EQ, chords.
import test from "node:test";
import assert from "node:assert/strict";

import { zoomAround, panBy, followPlayhead, MIN_SECONDS } from "../web/plugins/zoom/plugin.mjs";
import { gridLines, GAP } from "../web/plugins/beat-grid/plugin.mjs";
import { trimmed } from "../web/plugins/trim/plugin.mjs";
import { addPeaks, choice as practiceChoice, latencyKey } from "../web/plugins/practice/plugin.mjs";
import { findClick, settle } from "../web/plugins/practice/tuner.mjs";
import { match, onsets, summary } from "../web/plugins/practice/score.mjs";
import { addToRuns, takeNotes } from "../web/plugins/practice/plugin.mjs";
import { readBands, defaultBands, flat, xOfFrequency, frequencyOfX, yOfGain, gainOfY } from "../web/plugins/eq/plugin.mjs";
import { applyEdits, visibleLabels, LABEL_SPACE } from "../web/plugins/chords/plugin.mjs";
import { TUNINGS, frequency, choice } from "../web/plugins/tuner/plugin.mjs";

const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

test("zoom keeps the time under the pointer and stays inside the song", () => {
  const whole = { start: 0, end: 100 };
  const zoomed = zoomAround(whole, whole, 25, 0.5);
  close(zoomed.end - zoomed.start, 50);
  close((25 - zoomed.start) / 50, 0.25);  // 25 s is still a quarter in
  assert.deepEqual(zoomAround({ start: 40, end: 42 }, whole, 41, 0.1), { start: 41 - 0.5 * MIN_SECONDS, end: 41 + 0.5 * MIN_SECONDS });
  assert.deepEqual(zoomAround({ start: 90, end: 100 }, whole, 95, 10), whole);  // never longer than the song
  assert.deepEqual(panBy({ start: 90, end: 100 }, whole, 30), { start: 90, end: 100 });  // stops at the end
  assert.deepEqual(panBy({ start: 10, end: 20 }, { start: 5, end: 60 }, -30), { start: 5, end: 15 });  // and at a trim
});

test("zoom follows the playhead from the right edge on, 10 % from the left, and stops at the song's ends", () => {
  const bounds = { start: 0, end: 100 };
  assert.equal(followPlayhead({ start: 10, end: 20 }, bounds, 15, false), null);  // free inside the view
  assert.deepEqual(followPlayhead({ start: 10, end: 20 }, bounds, 20, false), { start: 19, end: 29 });  // the right edge
  assert.deepEqual(followPlayhead({ start: 19, end: 29 }, bounds, 22, true), { start: 21, end: 31 });  // then with it
  assert.deepEqual(followPlayhead({ start: 10, end: 20 }, bounds, 50, false), { start: 49, end: 59 });  // left the view
  assert.deepEqual(followPlayhead({ start: 89, end: 99 }, bounds, 95, true), { start: 90, end: 100 });  // the end
  assert.deepEqual(followPlayhead({ start: 0, end: 10 }, bounds, 0.5, true), { start: 0, end: 10 });  // the start
});

test("the grid shows the pale beats only when there is room for them", () => {
  const beats = Array.from({ length: 41 }, (_v, i) => i * 0.5);  // 120 BPM, 20 s
  const downbeats = beats.filter((_v, i) => i % 4 === 0);
  const wide = gridLines(beats, downbeats, { start: 0, end: 20 }, 2000);  // 50 px per beat
  assert.equal(wide.length, 41);
  assert.equal(wide.filter((line) => line.strong).length, 11);
  const narrow = gridLines(beats, downbeats, { start: 0, end: 20 }, 20 * 2 * (GAP - 1));
  assert.ok(narrow.every((line) => line.strong) && narrow.length === 11);
  // Zoomed out, bar starts thin out like the beats: every 2nd bar, then every 4th, then none.
  const bars = (beatPixels) => gridLines(beats, downbeats, { start: 0, end: 20 }, 40 * beatPixels).map((line) => line.x * 20);
  assert.deepEqual(bars(GAP / 4 + 0.5), beats.filter((_v, i) => i % 4 === 0));
  assert.deepEqual(bars(GAP / 8 + 0.1), beats.filter((_v, i) => i % 8 === 0));
  assert.deepEqual(bars(GAP / 16 + 0.1), beats.filter((_v, i) => i % 16 === 0));
  assert.deepEqual(bars(GAP / 16 - 0.1), []);
  // The same bars stay in a later view (counted from the song's first bar).
  assert.deepEqual(gridLines(beats, downbeats, { start: 3, end: 20 }, 17 * 2 * (GAP / 8 + 0.1)).map((line) => line.x * 17 + 3), [4, 8, 12, 16, 20]);
  const zoomed = gridLines(beats, [], { start: 5, end: 7 }, 500);  // no bar starts: every fourth beat
  assert.deepEqual(zoomed.map((line) => [line.x, line.strong]), [[0, false], [0.25, false], [0.5, true], [0.75, false], [1, false]]);
});

test("a trim cuts at the playhead and never leaves less than a second", () => {
  assert.deepEqual(trimmed(null, 100, "start", 12), { start: 12, end: 100 });
  assert.deepEqual(trimmed({ start: 12, end: 100 }, 100, "end", 80), { start: 12, end: 80 });
  assert.equal(trimmed({ start: 12, end: 80 }, 100, "start", 79.5), null);
  assert.equal(trimmed({ start: 12, end: 80 }, 100, "end", 5), null);
});

test("EQ bands: graph scales, flatness, saved bands checked", () => {
  close(xOfFrequency(20), 0);
  close(xOfFrequency(20000), 1);
  close(frequencyOfX(xOfFrequency(1234)), 1234);
  close(gainOfY(yOfGain(6.5)), 6.5);
  assert.equal(gainOfY(-1), 18);
  assert.ok(flat(defaultBands()));
  assert.deepEqual(defaultBands().map((band) => band.frequency), [50, 200, 800, 2600, 8000]);
  const bands = readBands([{ frequency: 60, gain: 30, q: 0 }, {}, { frequency: "x" }, {}, { gain: -3 }]);
  assert.deepEqual(bands.map((band) => band.type), ["lowshelf", "peaking", "peaking", "peaking", "highshelf"]);
  assert.deepEqual([bands[0].frequency, bands[0].gain, bands[0].q], [60, 18, 0.2]);
  assert.equal(bands[2].frequency, 800);
  assert.deepEqual(readBands([{}, {}, {}, { gain: 3 }]), defaultBands());  // four bands (before five): the default
  assert.ok(!flat(bands));
  assert.deepEqual(readBands("nonsense"), defaultBands());
});

test("chords: edits by start time, a name hidden only when the next chord's line would cover it", () => {
  const found = [{ start: 0, end: 2, chord: "C" }, { start: 2, end: 2.2, chord: "G" }, { start: 2.2, end: 4, chord: "Am" }];
  assert.deepEqual(applyEdits(found, { "2.000": "", "2.200": "A7" }).map((c) => c.chord), ["C", "A7"]);
  const wide = visibleLabels(found, { start: 0, end: 4 }, 400, () => 30);  // 100 px per second, names 30 px wide
  assert.deepEqual(wide.map((c) => c.chord), ["C", "G"]);  // Am starts 20 px after G: on G's name
  assert.deepEqual(visibleLabels(found, { start: 0, end: 4 }, 400, () => 15).map((c) => c.chord), ["C", "G", "Am"]);
  const zoomed = visibleLabels(found, { start: 1.9, end: 2.5 }, 600, () => 30);
  assert.deepEqual(zoomed.map((c) => c.chord), ["C", "G", "Am"]);
  assert.ok(zoomed[2].x * 600 >= zoomed[1].x * 600 + 30 + LABEL_SPACE);
});

test("tuner: equal temperament from A4 = 440 Hz, the six tunings, a stored choice checked", () => {
  close(frequency("A4"), 440);
  assert.ok(Math.abs(frequency("E2") - 82.407) < 0.001 && Math.abs(frequency("B0") - 30.868) < 0.001);
  assert.ok(Math.abs(frequency("E4") - 329.628) < 0.001 && Math.abs(frequency("D1") - 36.708) < 0.001);
  assert.deepEqual(Object.fromEntries(TUNINGS.map((tuning) => [tuning.id, tuning.notes.join(" ")])), {
    guitar: "E2 A2 D3 G3 B3 E4", "guitar-drop-d": "D2 A2 D3 G3 B3 E4", "guitar-7": "B1 E2 A2 D3 G3 B3 E4",
    bass: "E1 A1 D2 G2", "bass-drop-d": "D1 A1 D2 G2", "bass-5": "B0 E1 A1 D2 G2",
  });
  assert.deepEqual(choice({}), { tuning: TUNINGS[0], volume: 0.5 });
  assert.deepEqual(choice({ tuning: "bass-5", volume: 7 }), { tuning: TUNINGS[5], volume: 1 });
  assert.equal(choice({ tuning: "lute", volume: "x" }).tuning.id, "guitar");
});

test("practice: an input block raises the peaks of the song buckets it was heard in", () => {
  const peaks = new Float32Array(5).fill(-1);
  addPeaks(peaks, 0.1, 0.12, 0.32, Float32Array.from([0.1, -0.4, 0.2, 0.3]));  // 0.05 s a sample: buckets 1, 1, 2, 2
  assert.deepEqual([...peaks].map((v) => +v.toFixed(2)), [-1, 0.4, 0.3, -1, -1]);
  addPeaks(peaks, 0.1, 0.45, 0.65, Float32Array.from([0.5, 0.5]));  // past the end: only bucket 4
  assert.equal(+peaks[4].toFixed(2), 0.5);
  addPeaks(peaks, 0.1, 0.32, 0.37, Float32Array.from([0]));  // silence is recorded as 0, not "nothing yet"
  assert.equal(peaks[3], 0);
});

test("practice: settings fall back to the default input and a ±15 ms tolerance; a latency per input and output", () => {
  assert.deepEqual(practiceChoice({}), { input: "", tolerance: 15, latency: {} });
  assert.equal(practiceChoice({ tolerance: 90 }).tolerance, 40);
  assert.equal(latencyKey("", ""), "default>default");
  assert.equal(latencyKey("usb-1", "g7"), "usb-1>g7");
});

test("practice tuner: a click is found where it rises out of the noise, nothing in noise alone", () => {
  const samples = new Float32Array(1000).map((_, i) => (i % 2 ? 0.002 : -0.002));  // hum before and after
  for (let i = 437; i < 480; i++) samples[i] = 0.4 * Math.exp(-(i - 437) / 10) * (i % 2 ? 1 : -1);
  assert.equal(findClick(samples, 100), 437);
  assert.equal(findClick(new Float32Array(1000).fill(0.003), 100), -1);
  const loudNoise = new Float32Array(1000).map((_, i) => (i < 100 ? 0.2 : 0.25));  // nothing stands out
  assert.equal(findClick(loudNoise, 100), -1);
});

test("practice tuner: the estimate is the median; it stops when 5 agree within 0.8 ms, or on silence, or after 16", () => {
  assert.deepEqual(settle([], 1), { estimate: null, spread: null, stable: false, done: false, silent: false });
  assert.equal(settle([], 3).silent, true);
  const near = [0.0371, 0.0375, 0.0373, 0.0374, 0.0372, 0.0376];
  const state = settle(near, 6);
  assert.ok(state.stable && state.done);
  assert.equal(+state.estimate.toFixed(5), 0.03735);
  assert.ok(!settle(near.slice(0, 5), 5).done);  // at least 6 clicks
  assert.ok(!settle([0.03, 0.04, 0.035, 0.032, 0.038, 0.036], 6).done);  // wireless: they disagree
  assert.ok(settle([0.03, 0.04], 16).done);
});

// Plucked notes (110 Hz, decaying) at the given seconds, over a little noise.
function plucks(times, rate = 8000, seconds = 3) {
  const samples = new Float32Array(rate * seconds).map((_, i) => ((i * 7919) % 13 - 6) / 6000);
  for (const at of times) {
    for (let i = Math.round(at * rate), n = 0; i < samples.length && n < rate * 0.4; i++, n++) {
      samples[i] += 0.5 * Math.exp(-n / (rate * 0.1)) * Math.sin((2 * Math.PI * 110 * n) / rate);
    }
  }
  return samples;
}

test("practice score: note starts are found within a few ms, also a repeated note while the last one rings", () => {
  const times = [0.2, 0.5, 0.62, 1.0, 1.5, 2.25];
  const found = onsets(plucks(times), 8000);
  assert.equal(found.length, times.length);
  found.forEach((time, i) => assert.ok(Math.abs(time - times[i]) < 0.006, `${time} vs ${times[i]}`));
  assert.deepEqual(onsets(new Float32Array(8000), 8000), []);  // silence
});

test("practice score: each original note takes the nearest played note within 80 ms; counts by the tolerance", () => {
  const offsets = match([1, 2, 3, 4, 5], [0.99, 2.03, 2.05, 3.5, 5.0, 5.06]);
  assert.deepEqual(offsets.map((v) => (v === null ? null : Math.round(v * 1000))), [-10, 30, null, null, 0]);
  const result = summary(offsets, 0.015);
  assert.deepEqual({ ...result, mean: Math.round(result.mean * 1000) }, { notes: 5, onTime: 2, early: 0, late: 1, missed: 2, mean: 7, spread: result.spread });
  assert.equal(Math.round(match([1], [1.07, 0.95])[0] * 1000), -50);  // the closer of two
});

test("practice score: a take recorded in pieces is scored only where it was recorded", () => {
  const rate = 8000, samples = plucks([0.3, 0.8, 1.3, 1.8], rate, 2);
  const runs = [];
  for (let i = 0; i < samples.length; i += 1000) addToRuns(runs, 10 + i / rate, 10 + (i + 1000) / rate, samples.subarray(i, i + 1000));
  addToRuns(runs, 30, 30.125, new Float32Array(1000));  // after a seek: a run of its own
  assert.equal(runs.length, 2);
  const { played, covered } = takeNotes(runs, [10.3, 10.8, 11.3, 11.8, 12.5, 30.05, 31]);
  assert.deepEqual(covered, [10.3, 10.8, 11.3, 11.8]);  // 12.5 and 31 were not recorded; 30.05 is at a run's edge
  assert.equal(played.length, 4);
  match(covered, played).forEach((offset) => assert.ok(Math.abs(offset) < 0.006));
});

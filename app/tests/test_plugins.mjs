// The plugins' own logic (version 22), without a browser: zoom, beat grid, trim, EQ, chords.
import test from "node:test";
import assert from "node:assert/strict";

import { zoomAround, panBy, followPlayhead, MIN_SECONDS } from "../web/plugins/zoom/plugin.mjs";
import { gridLines, WEAK_GAP } from "../web/plugins/beat-grid/plugin.mjs";
import { trimmed } from "../web/plugins/trim/plugin.mjs";
import { readBands, defaultBands, flat, xOfFrequency, frequencyOfX, yOfGain, gainOfY } from "../web/plugins/eq/plugin.mjs";
import { applyEdits, visibleLabels, LABEL_SPACE } from "../web/plugins/chords/plugin.mjs";

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
  const narrow = gridLines(beats, downbeats, { start: 0, end: 20 }, 20 * 2 * (WEAK_GAP - 1));
  assert.ok(narrow.every((line) => line.strong) && narrow.length === 11);
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

import test from "node:test";
import assert from "node:assert/strict";
import { SynchronizedTransport } from "../web/playback.mjs";

class FakeAudio {
  constructor(duration = 10) {
    this.duration = duration;
    this.currentTime = 0;
    this.paused = true;
    this.pauseCalls = 0;
    this.playCalls = 0;
    this.rejectPlay = false;
  }
  play() {
    this.playCalls += 1;
    this.paused = false;
    return this.rejectPlay ? Promise.reject(new Error("blocked")) : Promise.resolve();
  }
  pause() {
    this.pauseCalls += 1;
    this.paused = true;
  }
}

function makeTransport(durations = [10, 10, 10, 10, 10, 10]) {
  const frames = new Map();
  let nextFrame = 1;
  const tracks = durations.map((duration) => new FakeAudio(duration));
  const transport = new SynchronizedTransport(tracks, {
    requestFrame(callback) { const id = nextFrame++; frames.set(id, callback); return id; },
    cancelFrame(id) { frames.delete(id); },
    driftTolerance: 0.08,
  });
  return { tracks, transport, tick() { const pending = [...frames.values()]; frames.clear(); pending.forEach((cb) => cb()); } };
}

test("play starts all tracks and seek synchronizes to the shortest duration", async () => {
  const { tracks, transport } = makeTransport([10, 8, 9, 10, 10, 10]);
  await transport.play();
  assert.deepEqual(tracks.map((track) => track.playCalls), [1, 1, 1, 1, 1, 1]);
  assert.equal(transport.seek(9), 8);
  assert.deepEqual(tracks.map((track) => track.currentTime), [8, 8, 8, 8, 8, 8]);
});

test("pause and stop apply to every track and stop resets shared position", async () => {
  const { tracks, transport } = makeTransport();
  await transport.play();
  transport.seek(4);
  transport.pause();
  assert.ok(tracks.every((track) => track.paused));
  transport.stop();
  assert.deepEqual(tracks.map((track) => track.currentTime), [0, 0, 0, 0, 0, 0]);
});

test("animation loop corrects slave drift to the master track", async () => {
  const { tracks, transport, tick } = makeTransport();
  await transport.play();
  tracks[0].currentTime = 2;
  tracks[1].currentTime = 2.5;
  tracks[2].currentTime = 2.04;
  tick();
  assert.equal(tracks[1].currentTime, 2);
  assert.equal(tracks[2].currentTime, 2.04);
});

test("a rejected play pauses every track and reports the failure", async () => {
  const { tracks, transport } = makeTransport();
  tracks[3].rejectPlay = true;
  await assert.rejects(transport.play(), /blocked/);
  assert.ok(tracks.every((track) => track.paused));
});

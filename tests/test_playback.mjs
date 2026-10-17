import test from "node:test";
import assert from "node:assert/strict";
import { MixerSession, SynchronizedTransport, snapFaderPercent } from "../web/playback.mjs";

class FakeAudio {
  constructor(duration = 10) {
    this.duration = duration;
    this.currentTime = 0;
    this.paused = true;
    this.pauseCalls = 0;
    this.playCalls = 0;
    this.volume = 1;
    this.muted = false;
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

function makeGainNodes(count = 6) {
  return Array.from({ length: count }, () => ({ gain: { value: 1 } }));
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

test("mixer gain is independent and supports 0 through 150 percent", () => {
  const nodes = makeGainNodes();
  const mixer = new MixerSession(nodes);
  mixer.setGain(0, 0.35);
  mixer.setGain(1, 1.5);
  assert.equal(nodes[0].gain.value, 0.35);
  assert.equal(nodes[1].gain.value, 1.5);
  assert.equal(nodes[2].gain.value, 1);
  mixer.setGain(2, -1);
  mixer.setGain(3, 2);
  assert.equal(nodes[2].gain.value, 0);
  assert.equal(nodes[3].gain.value, 1.5);
});

test("mute, multiple solo, and clearing solo compose with each track gain", () => {
  const nodes = makeGainNodes();
  const mixer = new MixerSession(nodes);
  mixer.setGain(0, 0.4);
  mixer.setGain(1, 0.7);
  mixer.setMute(0, true);
  mixer.setSolo(1, true);
  mixer.setSolo(2, true);
  assert.deepEqual(nodes.map((node) => node.gain.value), [0, 0.7, 1, 0, 0, 0]);
  mixer.setSolo(2, false);
  mixer.setSolo(1, false);
  assert.deepEqual(nodes.map((node) => node.gain.value), [0, 0.7, 1, 1, 1, 1]);
  mixer.setMute(0, false);
  assert.equal(nodes[0].gain.value, 0.4);
});

test("mixer reports which tracks S/M silence, independent of gain", () => {
  const reports = [];
  const mixer = new MixerSession(makeGainNodes(), (_gains, audible) => reports.push(audible));
  mixer.setGain(3, 0);
  assert.deepEqual(reports.at(-1), [true, true, true, true, true, true]);
  mixer.setMute(1, true);
  assert.deepEqual(reports.at(-1), [true, false, true, true, true, true]);
  mixer.setSolo(4, true);
  assert.deepEqual(reports.at(-1), [false, false, false, false, true, false]);
});

test("mixer updates preserve the current shared playback position", async () => {
  const { tracks, transport } = makeTransport();
  const mixer = new MixerSession(makeGainNodes());
  await transport.play();
  transport.seek(3.25);
  mixer.setGain(4, 0.2);
  mixer.setSolo(4, true);
  assert.deepEqual(tracks.map((track) => track.currentTime), [3.25, 3.25, 3.25, 3.25, 3.25, 3.25]);
});

test("fader snaps to 100 percent only inside the inclusive 95 to 105 percent band", () => {
  assert.equal(snapFaderPercent(0), 0);
  assert.equal(snapFaderPercent(94), 94);
  assert.equal(snapFaderPercent(95), 100);
  assert.equal(snapFaderPercent(100), 100);
  assert.equal(snapFaderPercent(105), 100);
  assert.equal(snapFaderPercent(106), 106);
  assert.equal(snapFaderPercent(150), 150);
});

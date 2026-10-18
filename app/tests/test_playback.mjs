import test from "node:test";
import assert from "node:assert/strict";
import { Metronome, MixerSession, SynchronizedTransport, firstBeatAtOrAfter, metronomeGain, snapFaderPercent } from "../web/playback.mjs";

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

test("pausing while the start is pending wins and is not reported as a failure", async () => {
  const { tracks, transport, tick } = makeTransport();
  tracks.forEach((track) => {
    track.play = () => { track.paused = false; return new Promise((_resolve, reject) => { track.abort = reject; }); };
  });
  const starting = transport.play();
  assert.equal(transport.playing, true);
  transport.pause();
  tracks.forEach((track) => track.abort(new Error("AbortError: play() was interrupted by pause()")));
  await starting;
  tick();
  assert.equal(transport.playing, false);
  assert.ok(tracks.every((track) => track.paused));
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

test("four-stem projects mix and play like six-stem ones", async () => {
  const nodes = makeGainNodes().slice(0, 4);
  const mixer = new MixerSession(nodes);
  mixer.setSolo(1, true);
  assert.deepEqual(nodes.map((node) => node.gain.value), [0, 1, 0, 0]);
  const tracks = [1, 2, 3, 4].map(() => new FakeAudio());
  const transport = new SynchronizedTransport(tracks, { requestFrame: () => 1, cancelFrame: () => {} });
  await transport.play();
  assert.ok(tracks.every((audio) => !audio.paused));
  assert.throws(() => new MixerSession([]), /1-6 mixer tracks/);
});

test("song time starts where the music starts: the leading silence is skipped", () => {
  const tracks = [new FakeAudio(10), new FakeAudio(10)];
  const seen = [];
  const transport = new SynchronizedTransport(tracks, { startOffset: 1.5, onTimeUpdate: (t) => seen.push(t), requestFrame: () => 1, cancelFrame: () => {} });
  assert.equal(transport.duration, 8.5);
  transport.seek(0);
  assert.deepEqual(tracks.map((audio) => audio.currentTime), [1.5, 1.5]);
  transport.seek(2);
  assert.equal(transport.currentTime, 2);
  assert.equal(tracks[1].currentTime, 3.5);
  transport.stop();
  assert.equal(tracks[0].currentTime, 1.5);
  assert.deepEqual(seen, [0, 2, 0]);
});

class FakeContext {
  constructor() { this.currentTime = 10; this.started = []; this.stopped = 0; }
  createOscillator() {
    const context = this;
    return { frequency: {}, connect() {}, start(when) { context.started.push(when); this.when = when; }, stop(when) { if (when === undefined) context.stopped += 1; } };
  }
  createGain() { return { gain: { setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} }; }
}

test("metronome clicks land on the song's beats, scheduled a little ahead on the audio clock", () => {
  const context = new FakeContext();
  const metronome = new Metronome(context, {}, { lookahead: 0.12 });
  metronome.setBeats([0, 0.5, 1.0, 1.5, 2.0]);
  metronome.update(0.45);  // beat 0.5 is 50 ms away
  assert.deepEqual(context.started.map((t) => +(t - 10).toFixed(3)), [0.05]);
  metronome.update(0.46);  // nothing new in the window, nothing scheduled twice
  context.currentTime = 10.5;
  metronome.update(0.95);
  assert.deepEqual(context.started.map((t) => +(t - 10).toFixed(3)), [0.05, 0.55]);
  assert.equal(metronome.clicks, 2);
});

test("a seek or pause drops scheduled clicks and restarts from the new position", () => {
  const context = new FakeContext();
  const metronome = new Metronome(context, {}, { lookahead: 0.12 });
  metronome.setBeats([0, 0.5, 1.0, 1.5, 2.0]);
  metronome.update(0.45);
  metronome.update(1.45);  // a jump forward (seek)
  assert.equal(context.stopped, 1);  // the 0.5 click that had not sounded yet is cancelled
  assert.deepEqual(context.started.slice(1).map((t) => +(t - 10).toFixed(3)), [0.05]);
  metronome.reset();
  metronome.update(0.0);  // back to the start (stop button)
  assert.equal(context.started.length, 3);
  assert.equal(firstBeatAtOrAfter([0, 0.5, 1.0], 0.6), 2);
});

test("metronome volume: 50 % matches the project, 100 % is twice as loud", () => {
  assert.equal(metronomeGain(0), 0);
  assert.equal(metronomeGain(50), 0.5);
  assert.equal(metronomeGain(100), 1);
  assert.equal(metronomeGain(150), 1);
});


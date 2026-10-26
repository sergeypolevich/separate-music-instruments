import test from "node:test";
import assert from "node:assert/strict";
import { Metronome, MixerSession, StretchEngine, SynchronizedTransport, countInPlan, firstBeatAtOrAfter, formatBpm, metronomeGain, rhythmFound, snapFaderPercent, tempoAt } from "../web/playback.mjs";

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

const round = (values) => values.map((value) => +value.toFixed(3));

test("count-in: a silent beat, four clicks, and the song's first beat lands on the fifth", () => {
  const plan = countInPlan([0, 0.5, 1, 1.5, 2, 2.5], 0, 100);
  assert.equal(plan.period, 0.5);
  assert.deepEqual(round(plan.clicks), [100.5, 101, 101.5, 102]);
  assert.equal(plan.audioStart, 102.5);
});

test("count-in follows the grid: a pickup before the first beat plays during the count-in", () => {
  const plan = countInPlan([0.4, 0.9, 1.4, 1.9, 2.4], 0, 100);  // music starts 0.4 s before the first beat
  assert.deepEqual(round(plan.clicks), [100.5, 101, 101.5, 102]);
  assert.equal(+plan.audioStart.toFixed(3), 102.1);
  const later = countInPlan([0.4, 0.9, 1.4, 1.9, 2.4], 1.0, 100);  // from a paused position: next beat 1.4
  assert.equal(+later.audioStart.toFixed(3), 102.1);
  const long = countInPlan([3, 3.5, 4, 4.5], 0, 100);  // a pickup longer than the count-in
  assert.equal(long.audioStart, 100);
  assert.deepEqual(round(long.clicks), [100.5, 101, 101.5, 102].map((t) => t + 0.5));
  assert.equal(countInPlan([1], 0, 100), null);
});

class FakeStretchNode {
  constructor() { this.inputTime = 0; this.schedules = []; }
  schedule(change) { this.schedules.push(change); if ("input" in change) this.inputTime = change.input; }
}

test("the stretch engine gives every stem node the same schedule", () => {
  const nodes = [new FakeStretchNode(), new FakeStretchNode()];
  const context = { currentTime: 5 };
  const engine = new StretchEngine(context, nodes);
  engine.rate = 0.5;
  engine.play(12);
  assert.ok(nodes.every((node) => JSON.stringify(node.schedules[0]) === JSON.stringify({ outputTime: 5, active: true, input: 12, rate: 0.5 })));
  context.currentTime = 8;  // 3 clock seconds at 50 % = 1.5 song seconds
  assert.equal(engine.position, 13.5);
  assert.equal(engine.pause(), 13.5);
  assert.ok(nodes.every((node) => node.schedules.at(-1).active === false));
  engine.seek(20);
  assert.equal(engine.position, 20);
});

test("the stretch position is what is heard, not the worklet's reported input time", () => {
  // The nodes report an input time ahead of the output by their latency; clicks timed from it came early.
  const nodes = [new FakeStretchNode()];
  const context = new FakeContext();  // the clock the metronome schedules on, at 10 s
  const engine = new StretchEngine(context, nodes);
  engine.rate = 0.25;
  engine.play(1.0);
  nodes[0].inputTime = 1.4;  // reported: latency ahead
  context.currentTime = 12;  // 2 clock seconds at 25 % = 0.5 song seconds
  assert.equal(engine.position, 1.5);
  engine.seek(3);  // a seek while playing restarts the schedule from the clock's now
  assert.deepEqual(nodes[0].schedules.at(-1), { outputTime: 12, input: 3, rate: 0.25 });
  context.currentTime = 16;
  assert.equal(engine.position, 4);
  // The metronome on the same clock: beat 5.0 is one song second away, four clock seconds at 25 %.
  const metronome = new Metronome(context, {}, { lookahead: 1 });
  metronome.rate = 0.25;
  metronome.setBeats([4, 5]);
  metronome.update(engine.position + 0.8);
  assert.deepEqual(context.started.map((t) => +(t - 16).toFixed(3)), [0.8]);
});

test("a speed change while playing hands the position to the stretch engine and back", async () => {
  const tracks = [new FakeAudio(30), new FakeAudio(30)];
  const transport = new SynchronizedTransport(tracks, { startOffset: 1, requestFrame: () => 1, cancelFrame: () => {} });
  const clock = { currentTime: 0 };
  transport.stretch = new StretchEngine(clock, [new FakeStretchNode(), new FakeStretchNode()]);
  transport.seek(4);
  await transport.play();
  await transport.setPlaybackRate(0.5);
  assert.ok(transport.stretching && tracks.every((audio) => audio.paused));  // the streams fall silent
  assert.equal(transport.stretch.nodes[0].schedules.at(-1).input, 5);      // file time = song 4 s + 1 s offset
  clock.currentTime = 4;  // 4 clock seconds at 50 % = 2 song seconds
  assert.equal(transport.currentTime, 6);
  await transport.setPlaybackRate(1);
  assert.ok(!transport.stretching && tracks.every((audio) => !audio.paused && audio.currentTime === 7));
});


test("at half speed the metronome and the count-in stretch with the music", () => {
  const context = new FakeContext();
  const metronome = new Metronome(context, {}, { lookahead: 0.12 });
  metronome.rate = 0.5;
  metronome.setBeats([0, 0.5, 1.0]);
  metronome.update(0.45);  // beat 0.5 is 0.05 song seconds away: 0.1 s on the clock at 50 %
  assert.deepEqual(context.started.map((t) => +(t - 10).toFixed(3)), [0.1]);
  const plan = countInPlan([0, 0.5, 1, 1.5, 2], 0, 100, 4, 0.5);
  assert.equal(plan.period, 1);
  assert.deepEqual(plan.clicks, [101, 102, 103, 104]);
  assert.equal(plan.audioStart, 105);
});

test("no rhythm below 40 or above 240 BPM or with too few beats", () => {
  const beats = (n, step) => Array.from({ length: n }, (_, i) => i * step);
  assert.equal(rhythmFound({ bpm: 120, beats: beats(20, 0.5) }), true);
  assert.equal(rhythmFound({ bpm: 40, beats: beats(20, 1.5) }), true);
  assert.equal(rhythmFound({ bpm: 240, beats: beats(20, 0.25) }), true);
  assert.equal(rhythmFound({ bpm: 20.7, beats: beats(6, 2.9) }), false);  // a spoken 16-second clip
  assert.equal(rhythmFound({ bpm: 260, beats: beats(40, 0.23) }), false);
  assert.equal(rhythmFound({ bpm: 120, beats: beats(7, 0.5) }), false);
  assert.equal(rhythmFound({ bpm: null, beats: [] }), false);
});

test("the tempo shown next to the metronome: the entry for the moment, at the playback speed, one decimal", () => {
  const tempo = [[0.3, 150.0], [1.9, 151.1234], [3.5, 149.96]];
  assert.equal(tempoAt(tempo, 0), 150.0);  // before the first entry: the first one
  assert.equal(tempoAt(tempo, 1.9), 151.1234);
  assert.equal(tempoAt(tempo, 3.4), 151.1234);
  assert.equal(tempoAt(tempo, 99), 149.96);
  assert.equal(tempoAt([], 1), null);
  assert.equal(formatBpm(150.0), "150 BPM");  // no trailing ".0"
  assert.equal(formatBpm(150.0123), "150 BPM");
  assert.equal(formatBpm(151.1234), "151.1 BPM");
  assert.equal(formatBpm(150, 0.5), "75 BPM");
  assert.equal(formatBpm(151.1, 1.25), "188.9 BPM");
  assert.equal(formatBpm(null), "");
});

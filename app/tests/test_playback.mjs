import test from "node:test";
import assert from "node:assert/strict";
import { BufferEngine, Metronome, snapLoop, MixerSession, PlayheadClock, StretchEngine, SynchronizedTransport, countInPlan, firstBeatAtOrAfter, formatBpm, metronomeGain, rhythmFound, snapFaderPercent, tempoAt } from "../web/playback.mjs";

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
    this.readyState = 4;  // HAVE_ENOUGH_DATA
    this.seeking = false;
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

test("a start cancelled by a pause and refused only after the next start never stops the new one", async () => {
  const { tracks, transport } = makeTransport();
  const aborts = [];
  tracks.forEach((track) => {
    track.play = () => { track.paused = false; return new Promise((resolve, reject) => { aborts.push(reject); track.resolve = resolve; }); };
  });
  const first = transport.play();
  transport.pause();
  tracks.forEach((track) => { track.play = () => { track.paused = false; return Promise.resolve(); }; });
  await transport.play();  // Space again before the browser refused the first start
  aborts.forEach((reject) => reject(new Error("AbortError: play() was interrupted by pause()")));
  await first;
  assert.equal(transport.playing, true);
  assert.ok(tracks.every((track) => !track.paused));
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
  assert.ok(nodes.every((node) => JSON.stringify(node.schedules[0]) === JSON.stringify({ outputTime: 5, active: true, input: 12, rate: 0.5, loopStart: 0, loopEnd: 0 })));
  context.currentTime = 8;  // 3 clock seconds at 50 % = 1.5 song seconds
  assert.equal(engine.position, 13.5);
  assert.equal(engine.pause(), 13.5);
  assert.ok(nodes.every((node) => node.schedules.at(-1).active === false));
  engine.seek(20);
  assert.equal(engine.position, 20);
});

test("the buffer engine starts every stem at one clock time and stops them on pause", () => {
  const started = [];
  const context = {
    currentTime: 5,
    createBufferSource() {
      const source = { connect: (output) => { source.output = output; }, disconnect() {}, start: (when, offset) => started.push({ source, when, offset }), stop() { source.stopped = true; } };
      return source;
    },
  };
  const engine = new BufferEngine(context, [{ duration: 200 }, { duration: 200 }], ["vocals", "drums"]);
  engine.pause();  // paused before play: nothing to stop
  engine.play(12);
  assert.deepEqual(started.map(({ source, when, offset }) => [source.output, when, offset]), [["vocals", 5, 12], ["drums", 5, 12]]);
  context.currentTime = 8;
  assert.equal(engine.pause(), 15);
  assert.ok(started.every(({ source }) => source.stopped));
  context.currentTime = 20;
  assert.equal(engine.position, 15);  // paused: the clock does not move it
  engine.seek(30);
  assert.equal(started.length, 2);  // a paused seek starts nothing
  engine.play(30);
  assert.deepEqual(started.slice(2).map(({ when, offset }) => [when, offset]), [[20, 30], [20, 30]]);
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
  assert.deepEqual(nodes[0].schedules.at(-1), { outputTime: 12, input: 3, rate: 0.25, loopStart: 0, loopEnd: 0 });
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

test("a stem still loading is never seeked after the others, and a stem is moved back in step at most once a second", async () => {
  let clock = 0;
  const frames = new Map();
  let nextFrame = 1;
  const tracks = [200, 200, 200, 200, 200, 200].map((duration) => new FakeAudio(duration));
  const transport = new SynchronizedTransport(tracks, {
    requestFrame(callback) { const id = nextFrame++; frames.set(id, callback); return id; },
    cancelFrame(id) { frames.delete(id); },
    now: () => clock,
  });
  const tick = () => { const pending = [...frames.values()]; frames.clear(); pending.forEach((cb) => cb()); };
  await transport.play();
  tracks.forEach((track) => { track.currentTime = 35.5; });
  tracks[2].currentTime = 35.3;
  tracks[2].readyState = 2;  // bass: its next chunk is late
  tick();
  tick();
  assert.equal(tracks[2].currentTime, 35.3);  // its pending load goes on
  assert.ok(tracks.every((track) => !track.paused));  // nothing else stops
  tracks[2].readyState = 4;  // the chunk arrived
  tick();
  assert.equal(tracks[2].currentTime, 35.5);  // back in step once it can play
  tracks[2].currentTime = 35.2;
  clock += 400;
  tick();
  assert.equal(tracks[2].currentTime, 35.2);  // not again within a second
  clock += 600;
  tick();
  assert.equal(tracks[2].currentTime, 35.5);
  assert.ok(tracks.every((track) => track.pauseCalls === 0));  // never paused and resumed by the sync
});

function clockedTransport() {
  const clock = { now: 0 };
  const frames = new Map();
  let nextFrame = 1;
  const tracks = [200, 200, 200, 200, 200, 200].map((duration) => new FakeAudio(duration));
  const timers = new Map();
  const transport = new SynchronizedTransport(tracks, {
    requestFrame(callback) { const id = nextFrame++; frames.set(id, callback); return id; },
    cancelFrame(id) { frames.delete(id); },
    now: () => clock.now,
    setTimer(callback) { const id = nextFrame++; timers.set(id, callback); return id; },
    clearTimer(id) { timers.delete(id); },
  });
  const tick = () => { const pending = [...frames.values()]; frames.clear(); pending.forEach((cb) => cb()); };
  const timer = () => { const pending = [...timers.values()]; timers.clear(); pending.forEach((cb) => cb()); };
  return { clock, tracks, transport, tick, timer };
}

test("a seek while playing stops all stems and starts them together once each has data there", async () => {
  const { clock, tracks, transport, tick } = clockedTransport();
  await transport.play();
  transport.seek(100);
  assert.ok(tracks.every((track) => track.paused) && transport.playing);  // still «playing» for the UI
  tracks.forEach((track, i) => { track.readyState = i === 3 ? 1 : 2; });  // the guitar still loading
  tick();
  assert.ok(tracks.every((track) => track.paused));  // nobody runs ahead
  tracks[3].readyState = 2;
  tick();
  assert.ok(tracks.every((track) => !track.paused));  // all start together
  assert.ok(tracks.every((track) => track.currentTime === 100));
  tracks[3].readyState = 1;
  tick();
  assert.ok(tracks.every((track) => !track.paused));  // once per seek: the sync never pauses them again
  // A stem that never gets its data holds the others back at most 3 s.
  transport.seek(50);
  tracks[2].readyState = 1;
  clock.now += 2900;
  tick();
  assert.ok(tracks.every((track) => track.paused));
  clock.now += 100;
  tick();
  assert.ok(tracks.every((track) => !track.paused));
});

test("a stem still loading mid-song neither freezes the shown time nor pulls the others back", async () => {
  const { tracks, transport, tick } = clockedTransport();
  const shown = [];
  transport.onTimeUpdate = (seconds) => shown.push(seconds);
  await transport.play();
  tracks.forEach((track) => { track.currentTime = 40; });
  tracks[0].readyState = 2;  // the first stem (vocals) waits for data: its time stands still
  tracks.slice(1).forEach((track) => { track.currentTime = 41.5; });
  tick();
  assert.equal(shown.at(-1), 41.5);  // the time follows the stems that play
  assert.ok(tracks.slice(1).every((track) => track.currentTime === 41.5));  // not pulled back to 40
  assert.equal(tracks[0].currentTime, 40);  // the loading one is left alone
  tracks[0].readyState = 4;
  tick();
  assert.equal(tracks[0].currentTime, 41.5);  // and joins the others once it can play
});

test("after a seek the stems start again on a timer too: a covered window gets no animation frames", async () => {
  const { tracks, transport, timer } = clockedTransport();
  await transport.play();
  transport.seek(80);
  tracks.forEach((track) => { track.readyState = 1; });
  timer();  // no animation frame at all from here on
  assert.ok(tracks.every((track) => track.paused));
  tracks.forEach((track) => { track.readyState = 2; });
  timer();
  assert.ok(tracks.every((track) => !track.paused && track.currentTime === 80));
  transport.seek(20);
  transport.pause();  // a pause while waiting cancels the restart
  tracks.forEach((track) => { track.readyState = 4; });
  timer();
  assert.ok(tracks.every((track) => track.paused));
});

test("a paused stem that moves on its own (Safari applying a late position) never shifts the song", async () => {
  const { tracks, transport } = makeTransport();
  await transport.play();
  transport.pause();
  transport.seek(0);
  tracks[0].currentTime = 0.39;  // arrives after the seek, the stem still paused
  tracks[1].currentTime = 0.41;
  assert.equal(transport.currentTime, 0);
  await transport.play();
  assert.deepEqual(tracks.map((track) => track.currentTime), [0, 0, 0, 0, 0, 0]);  // all start together at 0
});

test("the playhead runs on between the media clock's steps, never back, and jumps on a seek or a pause", () => {
  const clock = new PlayheadClock();
  assert.equal(clock.position(10, 0, true), 10);
  assert.ok(Math.abs(clock.position(10, 100, true) - 10.1) < 1e-9);  // currentTime has not moved yet
  assert.ok(Math.abs(clock.position(10, 200, true, 1.5) - 10.3) < 1e-9);  // at the playback rate
  assert.equal(clock.position(10.25, 250, true), 10.3);  // a reading a little behind: no step back
  assert.ok(Math.abs(clock.position(10.25, 350, true) - 10.35) < 1e-9);
  assert.ok(Math.abs(clock.position(10.25, 2000, true) - 10.55) < 1e-9);  // a stalled clock: at most 0.3 s ahead
  assert.equal(clock.position(42, 2010, true), 42);  // a seek
  assert.equal(clock.position(5, 2020, true), 5);  // back as well
  assert.equal(clock.position(5.01, 3000, false), 5.01);  // paused: exactly the reading
});

test("with alwaysStretch the stretch engine plays at 100 % too: play, pause and seek on its clock, the streams silent", async () => {
  const tracks = [new FakeAudio(30), new FakeAudio(30)];
  const transport = new SynchronizedTransport(tracks, { startOffset: 1, requestFrame: () => 1, cancelFrame: () => {} });
  const clock = { currentTime: 0 };
  transport.stretch = new StretchEngine(clock, [new FakeStretchNode(), new FakeStretchNode()]);
  assert.ok(!transport.stretching);  // attached, but 100 % streams unless asked
  transport.alwaysStretch = true;
  assert.ok(transport.stretching);
  transport.seek(4);
  await transport.play();
  assert.ok(tracks.every((audio) => audio.paused));
  assert.deepEqual(transport.stretch.nodes.map((node) => node.schedules.at(-1)), transport.stretch.nodes.map(() => ({ outputTime: 0, active: true, input: 5, rate: 1, loopStart: 0, loopEnd: 0 })));
  clock.currentTime = 3;
  assert.equal(transport.currentTime, 7);
  transport.pause();
  clock.currentTime = 10;  // paused: the position stays
  assert.equal(transport.currentTime, 7);
  await transport.play();  // resumed: every node from the same input at the same clock time
  assert.ok(transport.stretch.nodes.every((node) => node.schedules.at(-1).input === 8 && node.schedules.at(-1).outputTime === 10));
  transport.seek(20);
  assert.ok(transport.stretch.nodes.every((node) => node.schedules.at(-1).input === 21));
  await transport.setPlaybackRate(0.5);
  await transport.setPlaybackRate(1);
  assert.ok(transport.stretching && tracks.every((audio) => audio.paused));  // back at 100 %: still the engine
});

test("a dragged loop snaps both ends to the nearest beats, at least one beat long", () => {
  const beats = [0, 0.5, 1, 1.5, 2, 2.5];
  assert.deepEqual(snapLoop(0.55, 1.9, beats), { start: 0.5, end: 2 });  // a little past a beat: from that click
  assert.deepEqual(snapLoop(1.9, 0.55, beats), { start: 0.5, end: 2 });  // dragged leftwards
  assert.deepEqual(snapLoop(1.1, 1.2, beats), { start: 1, end: 1.5 });  // shorter than a beat: one beat
  assert.deepEqual(snapLoop(2.4, 9, beats), { start: 2, end: 2.5 });  // past the last beat
  assert.deepEqual(snapLoop(3, 1, []), { start: 1, end: 3 });  // no rhythm: as dragged
});

test("a loop plays round through the stretch engine; a seek out of it or the start drops it", async () => {
  const tracks = [new FakeAudio(30), new FakeAudio(30)];
  const changes = [];
  const transport = new SynchronizedTransport(tracks, { startOffset: 1, onLoopChange: (loop) => changes.push(loop), requestFrame: () => 1, cancelFrame: () => {} });
  const clock = { currentTime: 0 };
  transport.stretch = new StretchEngine(clock, [new FakeStretchNode(), new FakeStretchNode()]);
  transport.seek(20);
  await transport.setLoop({ start: 4, end: 6 });  // outside it: to its start
  assert.ok(transport.stretching && transport.currentTime === 4);
  await transport.play();
  assert.deepEqual(transport.stretch.nodes[0].schedules.at(-1), { outputTime: 0, active: true, input: 5, rate: 1, loopStart: 5, loopEnd: 7 });
  clock.currentTime = 2.5;  // 2.5 s from 4 in a 2 s loop: 4.5
  assert.equal(transport.currentTime, 4.5);
  transport.seek(5);  // inside: a seek within it
  assert.equal(transport.loop.start, 4);
  transport.seek(10);  // outside: the loop goes, playback goes on from there
  await Promise.resolve();
  assert.equal(transport.loop, null);
  assert.deepEqual(changes, [{ start: 4, end: 6 }, null]);
  assert.equal(transport.stretch.nodes[0].schedules.at(-1).active, false);  // the engine stops, the streams play on
  assert.ok(transport.playing && !transport.stretching);  // back to the streams at 100 %
  await transport.setLoop({ start: 0, end: 2 });
  transport.stop();  // ⏮ drops it even when it starts at 0
  assert.equal(transport.loop, null);
  assert.equal(transport.currentTime, 0);
});

test("the buffer engine loops its sources between the loop's ends", () => {
  const sources = [];
  const context = { currentTime: 0, createBufferSource() { const source = { connect() {}, disconnect() {}, start() {}, stop() {} }; sources.push(source); return source; } };
  const engine = new BufferEngine(context, [{ duration: 60 }], ["out"]);
  engine.loop = { start: 10, end: 12 };
  engine.play(11);
  assert.deepEqual([sources[0].loop, sources[0].loopStart, sources[0].loopEnd], [true, 10, 12]);
  context.currentTime = 3.5;  // 11 + 3.5 in a 2 s loop: 10.5
  assert.equal(engine.position, 10.5);
});

test("in a loop the metronome schedules the loop's first beats before the jump back, once", () => {
  const context = new FakeContext();  // clock at 10
  const metronome = new Metronome(context, {}, { lookahead: 0.12 });
  metronome.setBeats([0, 0.5, 1, 1.5, 2, 2.5]);
  metronome.loop = { start: 0.5, end: 1.5 };
  metronome.update(0.5);  // the loop's first beat now
  metronome.update(1.0);
  assert.deepEqual(context.started.map((t) => +(t - 10).toFixed(3)), [0, 0]);
  metronome.update(1.45);  // 0.05 s before the end: the click at 0.5 goes 0.05 s ahead; never the beat at 1.5
  assert.deepEqual(context.started.map((t) => +(t - 10).toFixed(3)), [0, 0, 0.05]);
  metronome.update(0.52);  // the jump back: nothing again for 0.5
  metronome.update(0.95);  // the beat at 1.0 in 0.05 s
  assert.deepEqual(context.started.map((t) => +(t - 10).toFixed(3)), [0, 0, 0.05, 0.05]);
});

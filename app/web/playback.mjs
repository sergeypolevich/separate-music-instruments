const STEM_ORDER = ["vocals", "drums", "bass", "guitar", "piano", "other"];

export class MixerSession {
  constructor(gainNodes, onEffectiveGain = () => {}) {
    // Four- and six-stem models are both supported.
    if (gainNodes.length < 1 || gainNodes.length > STEM_ORDER.length) {
      throw new Error(`Expected 1-${STEM_ORDER.length} mixer tracks`);
    }
    this.gainNodes = gainNodes;
    this.onEffectiveGain = onEffectiveGain;
    this.gains = gainNodes.map(() => 1);
    this.mutes = gainNodes.map(() => false);
    this.solos = gainNodes.map(() => false);
    this.apply();
  }

  setGain(index, gain) {
    this.assertIndex(index);
    const value = Number(gain);
    this.gains[index] = Number.isFinite(value) ? Math.min(1.5, Math.max(0, value)) : 0;
    this.apply();
  }

  setMute(index, muted) {
    this.assertIndex(index);
    this.mutes[index] = Boolean(muted);
    this.apply();
  }

  setSolo(index, solo) {
    this.assertIndex(index);
    this.solos[index] = Boolean(solo);
    this.apply();
  }

  assertIndex(index) {
    if (!Number.isInteger(index) || index < 0 || index >= this.gainNodes.length) {
      throw new RangeError("Mixer track index is out of range");
    }
  }

  apply() {
    const hasSolo = this.solos.some(Boolean);
    // Silenced by S/M (not by a gain of 0), so the UI can dim those waveforms.
    const audible = this.gainNodes.map((_node, index) => !this.mutes[index] && (!hasSolo || this.solos[index]));
    const effectiveGains = audible.map((isAudible, index) => (isAudible ? this.gains[index] : 0));
    this.gainNodes.forEach((node, index) => { node.gain.value = effectiveGains[index]; });
    this.onEffectiveGain(effectiveGains, audible);
  }
}

export function snapFaderPercent(percent) {
  const value = Number(percent);
  if (!Number.isFinite(value)) return 0;
  const clamped = Math.min(150, Math.max(0, value));
  return clamped >= 95 && clamped <= 105 ? 100 : clamped;
}

const HAVE_CURRENT_DATA = 2;  // HTMLMediaElement.readyState: the frame at the position (a paused stem stops here)
const HAVE_FUTURE_DATA = 3;  // enough data to play on
const CORRECTION_GAP_MS = 1000;  // a stem is moved back in step at most this often
const SEEK_WAIT_MS = 3000;  // after a seek while playing, all stems start together at most this late

export class SynchronizedTransport {
  constructor(tracks, {
    requestFrame = (callback) => requestAnimationFrame(callback),
    cancelFrame = (id) => cancelAnimationFrame(id),
    onTimeUpdate = () => {},
    onEnded = () => {},
    onLoopChange = () => {},
    beforePlay = () => Promise.resolve(),
    driftTolerance = 0.08,
    startOffset = 0,
    now = () => Date.now(),
    setTimer = (callback, ms) => setTimeout(callback, ms),
    clearTimer = (id) => clearTimeout(id),
  } = {}) {
    if (tracks.length < 1 || tracks.length > STEM_ORDER.length) {
      throw new Error(`Expected 1-${STEM_ORDER.length} synchronized tracks`);
    }
    this.tracks = tracks;
    this.requestFrame = requestFrame;
    this.cancelFrame = cancelFrame;
    this.onTimeUpdate = onTimeUpdate;
    this.onEnded = onEnded;
    this.beforePlay = beforePlay;
    this.driftTolerance = driftTolerance;
    this.now = now;
    this.corrected = new Map();  // track -> when it was last moved back in step
    this.resumeAfterSeek = null;  // when a seek while playing paused the stems; they start together (resumeIfReady)
    // A timer, not the animation frame: Chrome stops animation frames in a hidden or covered window, and the
    // stems must start again even when the user seeks and switches to another window.
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.resumeTimer = null;
    // Positions are song time: 0 is where the music starts, startOffset seconds into the files.
    this.startOffset = Math.max(0, Number(startOffset) || 0);
    // Speeds other than 100 % play the same stems through Signalsmith Stretch (see StretchEngine).
    this.rate = 1;
    this.stretch = null;
    // The stretch engine at 100 % too, once attached: Safari on iPhone resumes streamed stems one after another.
    this.alwaysStretch = false;
    // {start, end} in song time while a loop plays: always through the decoded engine (gapless, sample-locked).
    this.loop = null;
    this.onLoopChange = onLoopChange;
    this.frameId = null;
    this.playing = false;
    // File seconds while paused: Safari may move a paused stem later on its own (a position from before the
    // pause applied after a seek), so the paused position is kept here rather than read from the stems.
    this.pausedAt = null;
    this.playAttempt = 0;
    this.syncFrame = this.syncFrame.bind(this);
  }

  get mediaDuration() {
    const durations = this.tracks.map((track) => track.duration)
      .filter((duration) => Number.isFinite(duration) && duration >= 0);
    return durations.length === this.tracks.length ? Math.min(...durations) : 0;
  }

  get duration() {
    return Math.max(0, this.mediaDuration - this.startOffset);
  }

  get stretching() {
    return this.stretch !== null && (this.rate !== 1 || this.alwaysStretch || this.loop !== null);
  }

  inLoop(position) {
    return this.loop !== null && position >= this.loop.start && position < this.loop.end;
  }

  // Sets or drops the loop (song time; the stretch engine must be attached). A position outside it moves to its
  // start; `at` is where to go when dropping it. Playing goes on through the engine change, like a speed change.
  async setLoop(loop, at = null) {
    const position = at ?? this.currentTime;
    const wasPlaying = this.playing;
    if (wasPlaying) this.pause();
    this.loop = loop;
    if (this.stretch) this.stretch.loop = loop && { start: loop.start + this.startOffset, end: loop.end + this.startOffset };
    this.seek(loop && !this.inLoop(position) ? loop.start : position);
    this.onLoopChange(loop);
    if (wasPlaying) await this.play();
  }

  // The position heard: the median of the stems that are actually playing, so one stem still loading (whose time
  // stands still) neither freezes the shown time nor pulls the others back; the first stem when none plays.
  get mediaTime() {
    if (this.stretching) return this.stretch.position;
    if (!this.playing && this.pausedAt !== null) return this.pausedAt;
    const live = this.tracks.filter((track) => !track.paused && !track.seeking && track.readyState >= HAVE_FUTURE_DATA)
      .map((track) => track.currentTime).sort((a, b) => a - b);
    return live.length ? live[live.length >> 1] : this.tracks[0].currentTime;
  }

  get currentTime() {
    return Math.max(0, this.mediaTime - this.startOffset);
  }

  seek(seconds) {
    const requested = Number.isFinite(Number(seconds)) ? Number(seconds) : 0;
    const position = Math.min(Math.max(0, requested), this.duration || Math.max(0, requested));
    if (this.loop && !this.inLoop(position)) {  // a seek out of the loop drops it
      this.setLoop(null, position).catch(() => {});
      return position;
    }
    // While playing, the stems stop for the seek and start again together once each has data there (syncFrame);
    // otherwise those already loaded ran ahead while the others loaded, and were pulled back afterwards.
    if (this.playing && !this.stretching) {
      this.tracks.forEach((track) => track.pause());
      this.resumeAfterSeek = this.now();
      this.waitToResume();
    }
    // The stems keep the position in both engines, so a speed change never jumps.
    this.pausedAt = position + this.startOffset;
    this.tracks.forEach((track) => { track.currentTime = this.pausedAt; });
    if (this.stretching) this.stretch.seek(position + this.startOffset);
    this.onTimeUpdate(position);
    return position;
  }

  // Speed with the pitch kept: 100 % streams the stems as they are; any other speed hands them to the
  // stretch engine at the same position (it must be attached first, see StretchEngine).
  async setPlaybackRate(rate) {
    if (rate === this.rate) return;
    const position = this.currentTime;
    const wasPlaying = this.playing;
    if (wasPlaying) this.pause();
    this.rate = rate;
    if (this.stretch) this.stretch.rate = rate;
    this.seek(position);
    if (wasPlaying) await this.play();
  }

  async play() {
    if (this.playing) return;
    // Playing as soon as the start is requested, so a quick second toggle pauses instead of
    // starting again while the six tracks are still confirming playback.
    this.playing = true;
    if (this.stretching) {
      await this.beforePlay();
      if (!this.playing) return;
      this.stretch.play(this.pausedAt ?? this.tracks[0].currentTime);
      this.scheduleFrame();
      return;
    }
    if (this.pausedAt !== null) {
      this.tracks.forEach((track) => {
        if (Math.abs(track.currentTime - this.pausedAt) > this.driftTolerance) track.currentTime = this.pausedAt;
      });
    }
    // A start cancelled by a pause may be refused by the browser only after the next start: that refusal is old news.
    const attempt = ++this.playAttempt;
    try {
      const ready = this.beforePlay();
      // Invoke every play() in the same gesture before awaiting any promise.
      const playPromises = this.tracks.map((track) => track.play());
      await Promise.all([ready, ...playPromises]);
      if (this.playing) this.scheduleFrame();
    } catch (error) {
      if (!this.playing || attempt !== this.playAttempt) return; // paused while starting: an intentional stop, not a failure
      this.pause();
      throw error;
    }
  }

  pause() {
    const heard = this.mediaTime;
    this.playing = false;
    this.resumeAfterSeek = null;
    if (this.resumeTimer !== null) {
      this.clearTimer(this.resumeTimer);
      this.resumeTimer = null;
    }
    if (this.frameId !== null) {
      this.cancelFrame(this.frameId);
      this.frameId = null;
    }
    this.tracks.forEach((track) => track.pause());
    this.pausedAt = heard;
    if (this.stretching) {
      this.pausedAt = this.stretch.pause();
      this.tracks.forEach((track) => { track.currentTime = this.pausedAt; });
    }
  }

  stop() {
    this.pause();
    if (this.loop) this.setLoop(null, 0);
    else this.seek(0);
  }

  waitToResume() {
    if (this.resumeTimer !== null) this.clearTimer(this.resumeTimer);
    this.resumeTimer = this.setTimer(() => {
      this.resumeTimer = null;
      if (!this.resumeIfReady()) this.waitToResume();
    }, 50);
  }

  // After a seek while playing: start all stems together once each has data at the new position (a paused stem
  // loads only up to that frame, HAVE_CURRENT_DATA), or after SEEK_WAIT_MS anyway. Returns true when done.
  resumeIfReady() {
    if (this.resumeAfterSeek === null || !this.playing) return true;
    const ready = this.tracks.every((track) => !track.seeking && track.readyState >= HAVE_CURRENT_DATA);
    if (!ready && this.now() - this.resumeAfterSeek < SEEK_WAIT_MS) return false;
    this.resumeAfterSeek = null;  // once per seek: never paused again by the sync
    this.tracks.forEach((track) => { const started = track.play(); if (started) started.catch(() => {}); });
    return true;
  }

  scheduleFrame() {
    if (this.playing && this.frameId === null) {
      this.frameId = this.requestFrame(this.syncFrame);
    }
  }

  syncFrame() {
    this.frameId = null;
    if (!this.playing) return;
    const masterTime = this.mediaTime;
    if (this.stretching) {
      if (this.mediaDuration > 0 && masterTime >= this.mediaDuration) {
        this.pause();
        this.onEnded();
        return;
      }
      this.onTimeUpdate(Math.max(0, masterTime - this.startOffset));
      this.scheduleFrame();
      return;
    }
    if (this.tracks[0].ended || (this.mediaDuration > 0 && masterTime >= this.mediaDuration)) {
      this.pause();
      this.onEnded();
      return;
    }
    if (!this.resumeIfReady()) {  // still waiting after a seek (the timer also checks, see waitToResume)
      this.onTimeUpdate(Math.max(0, this.tracks[0].currentTime - this.startOffset));
      this.scheduleFrame();
      return;
    }
    // A stem still loading (a busy server answers its next chunk late, or a seek is under way) is left alone:
    // seeking it after the others aborted its pending load every frame and kept it silent for many seconds.
    // A stem that can play is moved back in step, but not more often than once a second.
    const now = this.now();
    for (const track of this.tracks) {
      if (track.seeking || track.readyState < HAVE_FUTURE_DATA) continue;
      if (Math.abs(track.currentTime - masterTime) > this.driftTolerance
          && now - (this.corrected.get(track) ?? -Infinity) >= CORRECTION_GAP_MS) {
        track.currentTime = masterTime;
        this.corrected.set(track, now);
      }
    }
    this.onTimeUpdate(Math.max(0, masterTime - this.startOffset));
    this.scheduleFrame();
  }
}

// Signalsmith Stretch nodes, one per stem, fed with the decoded stems and given identical schedules,
// so they stay sample-locked. Positions are file seconds.
export class StretchEngine {
  constructor(context, nodes) {
    this.context = context;
    this.nodes = nodes;
    this.rate = 1;
    this.playing = false;
    this.paused = 0;
    this.loop = null;  // {start, end} in file seconds
  }

  // decoded: AudioBuffers in stem order; outputs: the mixer's gain nodes, so S/M and faders still apply.
  static async create(context, createStretch, decoded, outputs, onNode = () => {}) {
    const nodes = [];
    for (const [index, buffer] of decoded.entries()) {
      const node = await createStretch(context);
      const channels = Array.from({ length: buffer.numberOfChannels }, (_, c) => buffer.getChannelData(c));
      await node.addBuffers(channels);
      await node.setUpdateInterval(0.02);
      node.connect(outputs[index]);
      nodes.push(node);
      onNode();
    }
    return new StretchEngine(context, nodes);
  }

  // The position heard now, from our own schedule and the AudioContext clock (the clock the metronome
  // uses). The nodes' reported inputTime runs ahead of what is heard by their latency (tenths of a
  // second at low speeds) and only updates every 20 ms, so it put the clicks early.
  get position() {
    if (!this.playing) return this.paused;
    return wrapLoop(this.segment.input + Math.max(0, this.context.currentTime - this.segment.output) * this.rate, this.loop);
  }

  // The nodes jump back from loopEnd to loopStart themselves; loopEnd 0 is no loop.
  get loopFields() {
    return { loopStart: this.loop ? this.loop.start : 0, loopEnd: this.loop ? this.loop.end : 0 };
  }

  play(input) {
    this.playing = true;
    this.paused = input;
    // outputTime (not the README's `output`) is the field this release reads for the start time.
    const when = this.context.currentTime;
    this.segment = { input, output: when };
    this.nodes.forEach((node) => node.schedule({ outputTime: when, active: true, input, rate: this.rate, ...this.loopFields }));
  }

  seek(input) {
    this.paused = input;
    if (!this.playing) return;
    const when = this.context.currentTime;
    this.segment = { input, output: when };
    this.nodes.forEach((node) => node.schedule({ outputTime: when, input, rate: this.rate, ...this.loopFields }));
  }

  pause() {
    const position = this.position;
    this.playing = false;
    this.paused = position;
    this.nodes.forEach((node) => node.schedule({ active: false, input: position }));
    return position;
  }
}

// A position played on past a loop's end, folded back into the loop (it repeats from its start).
function wrapLoop(position, loop) {
  if (!loop || position < loop.end) return position;
  return loop.start + ((position - loop.start) % (loop.end - loop.start));
}

// A loop dragged over the waveform with its ends moved to the nearest beats, so it starts and ends on a click;
// at least one beat long. Without beats it stays as dragged. Song time.
export function snapLoop(from, to, beats) {
  const start = Math.min(from, to);
  const end = Math.max(from, to);
  if (!beats || beats.length < 2) return { start, end };
  const nearest = (time) => {
    const index = firstBeatAtOrAfter(beats, time);
    if (index === 0) return 0;
    if (index === beats.length) return beats.length - 1;
    return time - beats[index - 1] <= beats[index] - time ? index - 1 : index;
  };
  let first = nearest(start);
  let last = nearest(end);
  if (last <= first) last = first + 1;
  if (last >= beats.length) {
    last = beats.length - 1;
    first = Math.min(first, last - 1);
  }
  return { start: beats[first], end: beats[last] };
}

// The decoded stems as plain buffer sources started at one AudioContext time: what a page over plain http
// can do (no AudioWorklet there, so no stretch). 100 % only; same interface as StretchEngine.
export class BufferEngine {
  constructor(context, buffers, outputs) {
    this.context = context;
    this.buffers = buffers;
    this.outputs = outputs;
    this.rate = 1;
    this.playing = false;
    this.paused = 0;
    this.sources = [];
    this.loop = null;  // {start, end} in file seconds
  }

  get position() {
    if (!this.playing) return this.paused;
    return wrapLoop(this.segment.input + Math.max(0, this.context.currentTime - this.segment.output), this.loop);
  }

  play(input) {
    this.playing = true;
    this.seek(input);
  }

  seek(input) {
    this.paused = input;
    if (!this.playing) return;
    this.stopSources();
    const when = this.context.currentTime;
    this.segment = { input, output: when };
    this.sources = this.buffers.map((buffer, index) => {
      const source = this.context.createBufferSource();
      source.buffer = buffer;
      if (this.loop) {
        source.loop = true;
        source.loopStart = this.loop.start;
        source.loopEnd = this.loop.end;
      }
      source.connect(this.outputs[index]);
      source.start(when, Math.min(input, buffer.duration));
      return source;
    });
  }

  pause() {
    const position = this.position;
    this.playing = false;
    this.paused = position;
    this.stopSources();
    return position;
  }

  stopSources() {
    for (const source of this.sources) {
      try { source.stop(); } catch (_error) { /* already ended */ }
      source.disconnect();
    }
    this.sources = [];
  }
}

// Metronome volume: 50 % is about as loud as the project, 100 % twice as loud.
export function metronomeGain(percent) {
  return (Math.min(100, Math.max(0, Number(percent) || 0)) / 50) * 0.5;
}

// Index of the first beat at or after a song time (beats are sorted).
// Speech or free-time music gives beats no metronome can follow (20 BPM on a spoken clip): show "no rhythm".
// The song's tempo at songTime from the analysis' [[time, bpm], ...] (every four beats; one entry when steady).
export function tempoAt(tempo, songTime) {
  if (!tempo || !tempo.length) return null;
  let low = 0;
  let high = tempo.length - 1;
  while (low < high) {  // the last entry at or before songTime
    const middle = (low + high + 1) >> 1;
    if (tempo[middle][0] <= songTime) low = middle;
    else high = middle - 1;
  }
  return tempo[low][1];
}

// "150 BPM", "151.1 BPM": the tempo heard at this playback speed, to one decimal without a trailing ".0".
export function formatBpm(bpm, rate = 1) {
  return bpm ? `${Math.round(bpm * rate * 10) / 10} BPM` : "";
}

export function rhythmFound(result) {
  return Boolean(result) && result.bpm >= 40 && result.bpm <= 240 && result.beats.length >= 8;
}

export function firstBeatAtOrAfter(beats, songTime) {
  let low = 0;
  let high = beats.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (beats[middle] < songTime - 1e-6) low = middle + 1;
    else high = middle;
  }
  return low;
}

// Count-in on the song's grid: the press is beat 0 (silent), then `count` clicks one beat apart, and
// the next beat after the last click is the song's first beat at or after `position`. Music before
// that beat (a pickup) starts during the count-in. Times are on the AudioContext clock.
export function countInPlan(beats, position, now, count = 4, rate = 1) {
  const index = Math.min(firstBeatAtOrAfter(beats, position), Math.max(0, beats.length - 2));
  if (beats.length < 2) return null;
  const gaps = beats.slice(index, index + 5).map((beat, i, near) => (i ? beat - near[i - 1] : null)).filter((gap) => gap > 0);
  // A song second lasts 1/rate clock seconds at a changed speed.
  const period = gaps.sort((a, b) => a - b)[gaps.length >> 1] / rate;
  let downbeat = now + (count + 1) * period;
  let audioStart = downbeat - Math.max(0, beats[index] - position) / rate;
  if (audioStart < now) {  // a pickup longer than the count-in: push everything later
    downbeat += now - audioStart;
    audioStart = now;
  }
  const clicks = Array.from({ length: count }, (_, i) => downbeat - (count - i) * period);
  return { clicks, audioStart, period };
}

// Clicks on the song's own beats, scheduled slightly ahead on the AudioContext clock: a timer alone
// would jitter. update() runs on every transport frame with the current song time.
export class Metronome {
  constructor(context, output, { lookahead = 0.12 } = {}) {
    this.context = context;
    this.output = output;
    this.lookahead = lookahead;
    this.rate = 1;  // playback speed: a song second lasts 1/rate clock seconds
    this.loop = null;  // {start, end} in song time: clicks go on from its start before the jump back
    this.beats = [];
    this.next = 0;
    this.wrapNext = null;  // the next beat scheduled after the loop's jump back
    this.lastSongTime = null;
    this.pending = [];
    this.clicks = 0;
  }

  setBeats(beats) {
    this.beats = beats;
    this.reset();
  }

  // Forget scheduled clicks (pause, seek, switching off); the next update picks up from there.
  reset() {
    const now = this.context.currentTime;
    for (const { oscillator, when } of this.pending) {
      if (when > now) {
        try { oscillator.stop(); } catch (_error) { /* already stopped */ }
      }
    }
    this.pending = [];
    this.lastSongTime = null;
    this.wrapNext = null;
  }

  update(songTime) {
    if (!this.beats.length) return;
    const loop = this.loop;
    const reach = this.lookahead * this.rate;
    // The loop's own jump back: the clicks after it are already scheduled, so it is not a seek.
    const wrapped = loop !== null && this.lastSongTime !== null && songTime < this.lastSongTime - 0.05
      && this.lastSongTime >= loop.end - reach - 0.25 && songTime <= loop.start + 0.25;
    const jumped = !wrapped && (this.lastSongTime === null || songTime < this.lastSongTime - 0.05 || songTime > this.lastSongTime + 0.5);
    if (jumped) {
      this.reset();
      this.next = firstBeatAtOrAfter(this.beats, songTime);
    }
    if (wrapped) {
      this.next = this.wrapNext ?? firstBeatAtOrAfter(this.beats, songTime);
      this.wrapNext = null;
    }
    this.lastSongTime = songTime;
    const now = this.context.currentTime;
    this.pending = this.pending.filter(({ when }) => when > now);
    const end = loop ? loop.end : Infinity;
    while (this.next < this.beats.length && this.beats[this.next] <= songTime + reach && this.beats[this.next] < end - 1e-6) {
      this.click(now + Math.max(0, this.beats[this.next] - songTime) / this.rate);
      this.next += 1;
    }
    if (loop && songTime + reach >= end) {  // the loop's first beats, heard right after its end
      if (this.wrapNext === null) this.wrapNext = firstBeatAtOrAfter(this.beats, loop.start);
      while (this.wrapNext < this.beats.length && this.beats[this.wrapNext] < end - 1e-6
          && this.beats[this.wrapNext] - loop.start <= songTime + reach - end) {
        this.click(now + (end - songTime + this.beats[this.wrapNext] - loop.start) / this.rate);
        this.wrapNext += 1;
      }
    }
  }

  click(when) {
    const oscillator = this.context.createOscillator();
    const envelope = this.context.createGain();
    oscillator.frequency.value = 1200;
    envelope.gain.setValueAtTime(0, when);
    envelope.gain.linearRampToValueAtTime(1, when + 0.002);
    envelope.gain.exponentialRampToValueAtTime(0.001, when + 0.05);
    oscillator.connect(envelope);
    envelope.connect(this.output);
    oscillator.start(when);
    oscillator.stop(when + 0.06);
    this.pending.push({ oscillator, when });
    this.clicks += 1;
  }
}

// Where to draw the playhead: Chrome moves a media element's currentTime in steps of up to a quarter second,
// so between them the line runs on by the frame clock at the playback rate. It never steps back (the next
// reading may be a little behind the guess), except on a seek or a pause, and guesses at most 0.3 s ahead.
export class PlayheadClock {
  constructor() {
    this.anchor = null;  // {reported, at}: the last new reading and when it came
    this.shown = 0;
  }

  position(reported, now, playing, rate = 1) {
    if (!playing || this.anchor === null || Math.abs(reported - this.shown) > 0.5) {
      this.anchor = playing ? { reported, at: now } : null;
      this.shown = reported;
      return reported;
    }
    if (reported !== this.anchor.reported) this.anchor = { reported, at: now };
    const elapsed = Math.min(0.3, (now - this.anchor.at) / 1000);
    this.shown = Math.max(this.shown, this.anchor.reported + elapsed * rate);
    return this.shown;
  }
}

export { STEM_ORDER };

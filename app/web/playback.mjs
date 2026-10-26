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

const HAVE_FUTURE_DATA = 3;  // HTMLMediaElement.readyState: enough data to play on
const CORRECTION_GAP_MS = 1000;  // a stem is moved back in step at most this often

export class SynchronizedTransport {
  constructor(tracks, {
    requestFrame = (callback) => requestAnimationFrame(callback),
    cancelFrame = (id) => cancelAnimationFrame(id),
    onTimeUpdate = () => {},
    onEnded = () => {},
    beforePlay = () => Promise.resolve(),
    driftTolerance = 0.08,
    startOffset = 0,
    now = () => Date.now(),
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
    // Positions are song time: 0 is where the music starts, startOffset seconds into the files.
    this.startOffset = Math.max(0, Number(startOffset) || 0);
    // Speeds other than 100 % play the same stems through Signalsmith Stretch (see StretchEngine).
    this.rate = 1;
    this.stretch = null;
    this.frameId = null;
    this.playing = false;
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
    return this.rate !== 1 && this.stretch !== null;
  }

  get mediaTime() {
    return this.stretching ? this.stretch.position : this.tracks[0].currentTime;
  }

  get currentTime() {
    return Math.max(0, this.mediaTime - this.startOffset);
  }

  seek(seconds) {
    const requested = Number.isFinite(Number(seconds)) ? Number(seconds) : 0;
    const position = Math.min(Math.max(0, requested), this.duration || Math.max(0, requested));
    // The stems keep the position in both engines, so a speed change never jumps.
    this.tracks.forEach((track) => { track.currentTime = position + this.startOffset; });
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
      this.stretch.play(this.tracks[0].currentTime);
      this.scheduleFrame();
      return;
    }
    try {
      const ready = this.beforePlay();
      // Invoke every play() in the same gesture before awaiting any promise.
      const playPromises = this.tracks.map((track) => track.play());
      await Promise.all([ready, ...playPromises]);
      if (this.playing) this.scheduleFrame();
    } catch (error) {
      if (!this.playing) return; // paused while starting: an intentional stop, not a failure
      this.pause();
      throw error;
    }
  }

  pause() {
    this.playing = false;
    if (this.frameId !== null) {
      this.cancelFrame(this.frameId);
      this.frameId = null;
    }
    this.tracks.forEach((track) => track.pause());
    if (this.stretching) {
      const position = this.stretch.pause();
      this.tracks.forEach((track) => { track.currentTime = position; });
    }
  }

  stop() {
    this.pause();
    this.seek(0);
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
    // A stem still loading (a busy server answers its next chunk late, or a seek is under way) is left alone:
    // seeking it after the others aborted its pending load every frame and kept it silent for many seconds.
    // A stem that can play is moved back in step, but not more often than once a second.
    const now = this.now();
    for (const track of this.tracks.slice(1)) {
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
    return this.segment.input + Math.max(0, this.context.currentTime - this.segment.output) * this.rate;
  }

  play(input) {
    this.playing = true;
    this.paused = input;
    // outputTime (not the README's `output`) is the field this release reads for the start time.
    const when = this.context.currentTime;
    this.segment = { input, output: when };
    this.nodes.forEach((node) => node.schedule({ outputTime: when, active: true, input, rate: this.rate }));
  }

  seek(input) {
    this.paused = input;
    if (!this.playing) return;
    const when = this.context.currentTime;
    this.segment = { input, output: when };
    this.nodes.forEach((node) => node.schedule({ outputTime: when, input, rate: this.rate }));
  }

  pause() {
    const position = this.position;
    this.playing = false;
    this.paused = position;
    this.nodes.forEach((node) => node.schedule({ active: false, input: position }));
    return position;
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
    this.beats = [];
    this.next = 0;
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
  }

  update(songTime) {
    if (!this.beats.length) return;
    const jumped = this.lastSongTime === null || songTime < this.lastSongTime - 0.05 || songTime > this.lastSongTime + 0.5;
    if (jumped) {
      this.reset();
      this.next = firstBeatAtOrAfter(this.beats, songTime);
    }
    this.lastSongTime = songTime;
    const now = this.context.currentTime;
    this.pending = this.pending.filter(({ when }) => when > now);
    while (this.next < this.beats.length && this.beats[this.next] <= songTime + this.lookahead * this.rate) {
      this.click(now + Math.max(0, this.beats[this.next] - songTime) / this.rate);
      this.next += 1;
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

export { STEM_ORDER };

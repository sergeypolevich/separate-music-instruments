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

export class SynchronizedTransport {
  constructor(tracks, {
    requestFrame = (callback) => requestAnimationFrame(callback),
    cancelFrame = (id) => cancelAnimationFrame(id),
    onTimeUpdate = () => {},
    onEnded = () => {},
    beforePlay = () => Promise.resolve(),
    driftTolerance = 0.08,
    startOffset = 0,
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
    // Positions are song time: 0 is where the music starts, startOffset seconds into the files.
    this.startOffset = Math.max(0, Number(startOffset) || 0);
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

  get currentTime() {
    return Math.max(0, this.tracks[0].currentTime - this.startOffset);
  }

  seek(seconds) {
    const requested = Number.isFinite(Number(seconds)) ? Number(seconds) : 0;
    const position = Math.min(Math.max(0, requested), this.duration || Math.max(0, requested));
    this.tracks.forEach((track) => { track.currentTime = position + this.startOffset; });
    this.onTimeUpdate(position);
    return position;
  }

  async play() {
    if (this.playing) return;
    // Playing as soon as the start is requested, so a quick second toggle pauses instead of
    // starting again while the six tracks are still confirming playback.
    this.playing = true;
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
    const masterTime = this.tracks[0].currentTime;
    if (this.tracks[0].ended || (this.mediaDuration > 0 && masterTime >= this.mediaDuration)) {
      this.pause();
      this.onEnded();
      return;
    }
    for (const track of this.tracks.slice(1)) {
      if (Math.abs(track.currentTime - masterTime) > this.driftTolerance) {
        track.currentTime = masterTime;
      }
    }
    this.onTimeUpdate(Math.max(0, masterTime - this.startOffset));
    this.scheduleFrame();
  }
}

// Metronome volume: 50 % is about as loud as the project, 100 % twice as loud.
export function metronomeGain(percent) {
  return (Math.min(100, Math.max(0, Number(percent) || 0)) / 50) * 0.5;
}

// Index of the first beat at or after a song time (beats are sorted).
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

// Clicks on the song's own beats, scheduled slightly ahead on the AudioContext clock: a timer alone
// would jitter. update() runs on every transport frame with the current song time.
export class Metronome {
  constructor(context, output, { lookahead = 0.12 } = {}) {
    this.context = context;
    this.output = output;
    this.lookahead = lookahead;
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
    while (this.next < this.beats.length && this.beats[this.next] <= songTime + this.lookahead) {
      this.click(now + Math.max(0, this.beats[this.next] - songTime));
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

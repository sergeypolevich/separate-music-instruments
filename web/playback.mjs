const STEM_ORDER = ["vocals", "drums", "bass", "guitar", "piano", "other"];

export class MixerSession {
  constructor(gainNodes, onEffectiveGain = () => {}) {
    if (gainNodes.length !== STEM_ORDER.length) {
      throw new Error(`Expected ${STEM_ORDER.length} mixer tracks`);
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
    const effectiveGains = this.gainNodes.map((_node, index) => {
      const audible = !this.mutes[index] && (!hasSolo || this.solos[index]);
      return audible ? this.gains[index] : 0;
    });
    this.gainNodes.forEach((node, index) => { node.gain.value = effectiveGains[index]; });
    this.onEffectiveGain(effectiveGains);
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
  } = {}) {
    if (tracks.length !== STEM_ORDER.length) {
      throw new Error(`Expected ${STEM_ORDER.length} synchronized tracks`);
    }
    this.tracks = tracks;
    this.requestFrame = requestFrame;
    this.cancelFrame = cancelFrame;
    this.onTimeUpdate = onTimeUpdate;
    this.onEnded = onEnded;
    this.beforePlay = beforePlay;
    this.driftTolerance = driftTolerance;
    this.frameId = null;
    this.playing = false;
    this.syncFrame = this.syncFrame.bind(this);
  }

  get duration() {
    const durations = this.tracks.map((track) => track.duration)
      .filter((duration) => Number.isFinite(duration) && duration >= 0);
    return durations.length === this.tracks.length ? Math.min(...durations) : 0;
  }

  seek(seconds) {
    const requested = Number.isFinite(Number(seconds)) ? Number(seconds) : 0;
    const position = Math.min(Math.max(0, requested), this.duration || Math.max(0, requested));
    this.tracks.forEach((track) => { track.currentTime = position; });
    this.onTimeUpdate(position);
    return position;
  }

  async play() {
    if (this.playing) return;
    try {
      const ready = this.beforePlay();
      // Invoke every play() in the same gesture before awaiting any promise.
      const playPromises = this.tracks.map((track) => track.play());
      await Promise.all([ready, ...playPromises]);
      this.playing = true;
      this.scheduleFrame();
    } catch (error) {
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
    if (this.tracks[0].ended || (this.duration > 0 && masterTime >= this.duration)) {
      this.pause();
      this.onEnded();
      return;
    }
    for (const track of this.tracks.slice(1)) {
      if (Math.abs(track.currentTime - masterTime) > this.driftTolerance) {
        track.currentTime = masterTime;
      }
    }
    this.onTimeUpdate(masterTime);
    this.scheduleFrame();
  }
}

export { STEM_ORDER };

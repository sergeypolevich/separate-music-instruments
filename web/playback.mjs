const STEM_ORDER = ["vocals", "drums", "bass", "guitar", "piano", "other"];

export class SynchronizedTransport {
  constructor(tracks, {
    requestFrame = (callback) => requestAnimationFrame(callback),
    cancelFrame = (id) => cancelAnimationFrame(id),
    onTimeUpdate = () => {},
    onEnded = () => {},
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
      // Invoke every play() in the same gesture before awaiting any promise.
      await Promise.all(this.tracks.map((track) => track.play()));
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

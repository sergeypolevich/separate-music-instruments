// The practice plugin's capture: the audio input in blocks of 1024 frames, each with the AudioContext frame of its
// first sample, so the page maps it to song time on the same clock the stems play on.
class PracticeCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.block = new Float32Array(1024);
    this.filled = 0;
    this.first = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i++) {
      if (this.filled === 0) this.first = currentFrame + i;
      this.block[this.filled++] = channel[i];
      if (this.filled === this.block.length) {
        this.port.postMessage({ frame: this.first, samples: this.block }, [this.block.buffer]);
        this.block = new Float32Array(1024);
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor("practice-capture", PracticeCapture);

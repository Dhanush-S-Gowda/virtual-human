/** Batch mono PCM16 into 100 ms packets. Flush the last packet before end. */
class PCMCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = [];
    this.enabled = true;
    this.port.onmessage = ({ data }) => {
      if (data === "stop") {
        this.enabled = false;
        this.flush();
        this.port.postMessage({ stopped: true });
      }
    };
  }

  flush() {
    if (!this.samples.length) return;
    const buffer = new ArrayBuffer(this.samples.length * 2);
    const view = new DataView(buffer);
    let energy = 0;
    for (let index = 0; index < this.samples.length; index++) {
      const sample = Math.max(-1, Math.min(1, this.samples[index]));
      view.setInt16(index * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
      energy += sample * sample;
    }
    this.port.postMessage({ buffer, rms: Math.sqrt(energy / this.samples.length),
      durationMs: this.samples.length / sampleRate * 1000 }, [buffer]);
    this.samples = [];
  }

  process(inputs) {
    const channels = inputs[0];
    if (this.enabled && channels?.length) {
      for (let index = 0; index < channels[0].length; index++) {
        this.samples.push(channels.reduce((sum, channel) => sum + channel[index], 0) / channels.length);
      }
      if (this.samples.length >= sampleRate / 10) this.flush();
    }
    return true;
  }
}

registerProcessor("pcm-capture", PCMCapture);

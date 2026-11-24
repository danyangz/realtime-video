// Mic capture: low-pass + resample from the AudioContext rate to 16 kHz, emit PCM16 in 20 ms chunks.
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.outRate = 16000;
    this.ratio = sampleRate / this.outRate;
    // Two cascaded one-pole low-pass filters (~7 kHz) to limit aliasing before decimation.
    this.a = 1 - Math.exp((-2 * Math.PI * 7000) / sampleRate);
    this.y1 = 0;
    this.y2 = 0;
    this.pos = 0; // fractional index of the next output sample, relative to the current block
    this.last = 0; // last filtered sample of the previous block
    this.chunk = 320; // 20 ms at 16 kHz
    this.out = new Int16Array(this.chunk);
    this.outIdx = 0;
    this.buf = null;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || !input[0]) return true;
    const x = input[0];
    const n = x.length;
    if (!this.buf || this.buf.length !== n) this.buf = new Float32Array(n);

    let y1 = this.y1, y2 = this.y2;
    const a = this.a;
    for (let i = 0; i < n; i++) {
      y1 += a * (x[i] - y1);
      y2 += a * (y1 - y2);
      this.buf[i] = y2;
    }
    this.y1 = y1;
    this.y2 = y2;

    let pos = this.pos;
    while (pos < n - 1) {
      const i0 = Math.floor(pos);
      const frac = pos - i0;
      const s0 = i0 < 0 ? this.last : this.buf[i0];
      const s1 = this.buf[i0 + 1];
      let v = s0 + (s1 - s0) * frac;
      v = v > 1 ? 1 : v < -1 ? -1 : v;
      this.out[this.outIdx++] = v * 0x7fff;
      if (this.outIdx === this.chunk) {
        this.port.postMessage(this.out.buffer, [this.out.buffer]);
        this.out = new Int16Array(this.chunk);
        this.outIdx = 0;
      }
      pos += this.ratio;
    }
    this.pos = pos - n;
    this.last = this.buf[n - 1];
    return true;
  }
}

registerProcessor('capture', CaptureProcessor);

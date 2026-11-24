// TTS playback: queue PCM16 chunks (e.g. 24 kHz), resample to the AudioContext rate, play gaplessly.
// Messages in:  {type:'start', rate}  {type:'audio', data: ArrayBuffer}  {type:'end'}  {type:'flush'}
// Messages out: {type:'empty', value: bool}  (true when nothing is queued or playing)
class PlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.setRate(24000);
    this.queue = [];
    this.cur = null;
    this.curIdx = 0;
    this.buffered = 0; // source samples waiting
    this.playing = false;
    this.ending = false; // server said no more audio for this turn
    this.a = 0;
    this.b = 0;
    this.frac = 0;
    this.empty = true;
    this.port.onmessage = (e) => this.onMessage(e.data);
  }

  setRate(rate) {
    this.srcRate = rate;
    this.step = rate / sampleRate;
    this.prebuffer = Math.round(rate * 0.08); // wait for 80 ms before starting, to ride out network jitter
  }

  onMessage(m) {
    if (m.type === 'audio') {
      const pcm = new Int16Array(m.data);
      const f = new Float32Array(pcm.length);
      for (let i = 0; i < pcm.length; i++) f[i] = pcm[i] / 32768;
      this.queue.push(f);
      this.buffered += f.length;
    } else if (m.type === 'start') {
      this.clear();
      if (m.rate) this.setRate(m.rate);
    } else if (m.type === 'end') {
      this.ending = true;
    } else if (m.type === 'flush') {
      this.clear();
    }
  }

  clear() {
    this.queue = [];
    this.cur = null;
    this.curIdx = 0;
    this.buffered = 0;
    this.playing = false;
    this.ending = false;
    this.frac = 0;
    this.a = 0;
    this.b = 0;
  }

  pull() {
    while (this.cur === null || this.curIdx >= this.cur.length) {
      this.cur = this.queue.shift() || null;
      this.curIdx = 0;
      if (this.cur === null) return 0;
    }
    this.buffered--;
    return this.cur[this.curIdx++];
  }

  process(_inputs, outputs) {
    const output = outputs[0];
    const out = output[0];
    const n = out.length;

    if (!this.playing && this.buffered > 0 && (this.buffered >= this.prebuffer || this.ending)) {
      this.playing = true;
      this.a = 0;
      this.b = this.pull();
      this.frac = 0;
    }

    for (let i = 0; i < n; i++) {
      if (!this.playing) {
        out[i] = 0;
        continue;
      }
      out[i] = this.a + (this.b - this.a) * this.frac;
      this.frac += this.step;
      while (this.frac >= 1) {
        if (this.buffered === 0) {
          this.playing = false; // underrun or end of turn
          this.frac = 0;
          break;
        }
        this.frac -= 1;
        this.a = this.b;
        this.b = this.pull();
      }
    }
    for (let c = 1; c < output.length; c++) output[c].set(out);

    const isEmpty = !this.playing && this.buffered === 0;
    if (isEmpty !== this.empty) {
      this.empty = isEmpty;
      this.port.postMessage({ type: 'empty', value: isEmpty });
    }
    return true;
  }
}

registerProcessor('playback', PlaybackProcessor);

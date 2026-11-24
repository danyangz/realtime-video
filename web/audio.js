// Audio I/O: microphone capture (-> 16 kHz PCM16) and TTS playback, plus level analysis for the avatar.

export class AudioEngine {
  constructor() {
    this.ctx = null;
    this.stream = null;
    this.playerEmpty = true;
    this.onPlayerEmpty = null;
    this._freq = null;
    this._time = null;
    this._micTime = null;
  }

  // Must be called from a user gesture (click), so the browser allows audio.
  async init() {
    if (this.ctx) {
      await this.ctx.resume();
      return;
    }
    this.ctx = new AudioContext({ latencyHint: 'interactive' });
    await this.ctx.resume();
    await this.ctx.audioWorklet.addModule('worklets/capture.js');
    await this.ctx.audioWorklet.addModule('worklets/playback.js');

    this.player = new AudioWorkletNode(this.ctx, 'playback', { numberOfInputs: 0, outputChannelCount: [1] });
    this.player.port.onmessage = (e) => {
      if (e.data.type === 'empty') {
        this.playerEmpty = e.data.value;
        if (this.onPlayerEmpty) this.onPlayerEmpty(e.data.value);
      }
    };
    this.outAnalyser = this.ctx.createAnalyser();
    this.outAnalyser.fftSize = 1024;
    this.outAnalyser.smoothingTimeConstant = 0.2;
    this.player.connect(this.outAnalyser);
    this.outAnalyser.connect(this.ctx.destination);
    this._freq = new Uint8Array(this.outAnalyser.frequencyBinCount);
    this._time = new Float32Array(this.outAnalyser.fftSize);
  }

  async startMic(onPcm) {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    this.micSource = this.ctx.createMediaStreamSource(this.stream);
    this.capture = new AudioWorkletNode(this.ctx, 'capture', { numberOfInputs: 1, numberOfOutputs: 1 });
    this.capture.port.onmessage = (e) => onPcm(e.data);
    // Keep the capture node pulled by the graph without making any sound.
    this.sink = this.ctx.createGain();
    this.sink.gain.value = 0;
    this.micSource.connect(this.capture).connect(this.sink).connect(this.ctx.destination);

    this.micAnalyser = this.ctx.createAnalyser();
    this.micAnalyser.fftSize = 512;
    this.micSource.connect(this.micAnalyser);
    this._micTime = new Float32Array(this.micAnalyser.fftSize);
  }

  stopMic() {
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    for (const node of [this.micSource, this.capture, this.sink, this.micAnalyser]) {
      if (node) node.disconnect();
    }
    this.stream = this.micSource = this.capture = this.sink = this.micAnalyser = null;
  }

  setMicMuted(muted) {
    // A disabled track produces silence, so the transcription session stays alive.
    if (this.stream) this.stream.getAudioTracks().forEach((t) => (t.enabled = !muted));
  }

  startTurn(rate) {
    this.playerEmpty = true;
    this.player.port.postMessage({ type: 'start', rate });
  }

  play(pcmBuffer) {
    this.playerEmpty = false;
    this.player.port.postMessage({ type: 'audio', data: pcmBuffer }, [pcmBuffer]);
  }

  endTurn() {
    this.player.port.postMessage({ type: 'end' });
  }

  flush() {
    if (this.player) this.player.port.postMessage({ type: 'flush' });
  }

  // Loudness and a rough "brightness" of the avatar's voice, for lip sync.
  outputLevels() {
    if (!this.outAnalyser) return { rms: 0, bright: 0 };
    this.outAnalyser.getFloatTimeDomainData(this._time);
    let sum = 0;
    for (let i = 0; i < this._time.length; i++) sum += this._time[i] * this._time[i];
    const rms = Math.sqrt(sum / this._time.length);

    this.outAnalyser.getByteFrequencyData(this._freq);
    const hz = this.ctx.sampleRate / this.outAnalyser.fftSize;
    const band = (lo, hi) => {
      let s = 0, c = 0;
      for (let i = Math.floor(lo / hz); i <= Math.ceil(hi / hz) && i < this._freq.length; i++) {
        s += this._freq[i];
        c++;
      }
      return c ? s / c : 0;
    };
    const low = band(150, 900);
    const high = band(1200, 4000);
    const bright = low + high > 1 ? high / (low + high) : 0;
    return { rms, bright };
  }

  micLevel() {
    if (!this.micAnalyser) return 0;
    this.micAnalyser.getFloatTimeDomainData(this._micTime);
    let sum = 0;
    for (let i = 0; i < this._micTime.length; i++) sum += this._micTime[i] * this._micTime[i];
    return Math.sqrt(sum / this._micTime.length);
  }
}

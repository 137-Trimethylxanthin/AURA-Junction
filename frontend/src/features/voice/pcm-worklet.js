// AudioWorklet for the live voice co-pilot: downsamples the mic to 16 kHz Int16 PCM and posts ~100 ms chunks.
// A bundled file (not a blob: URL) so it loads under the deploy CSP `script-src 'self'`.
const OUT_RATE = 16000

class AuraPcm extends AudioWorkletProcessor {
  constructor() {
    super()
    this.ratio = sampleRate / OUT_RATE
    this.acc = 0
    this.buf = new Int16Array(1600)
    this.n = 0
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0]
    if (!ch) return true
    for (let i = 0; i < ch.length; i++) {
      this.acc += 1
      if (this.acc >= this.ratio) {
        this.acc -= this.ratio
        const s = Math.max(-1, Math.min(1, ch[i]))
        this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff
        if (this.n === this.buf.length) {
          this.port.postMessage(this.buf.slice(0).buffer)
          this.n = 0
        }
      }
    }
    return true
  }
}
registerProcessor('aura-pcm', AuraPcm)

import { useCallback, useEffect, useRef, useState } from 'react'
import type { Lang } from '@/api/contract'
import { tu } from '@/i18n/uiLang'
import { VoiceApiError, mintLiveToken } from './voiceApi'

/**
 * Live voice-to-voice co-pilot over the Gemini Live API (raw WebSocket, gemini-3.8-live).
 * The backend mints a single-use ephemeral token (`POST /voice/token`) already locked to the model, the
 * system prompt and the one tool, so the API key never reaches the browser. Mic audio goes up as 16 kHz
 * 16-bit PCM; the model's 24 kHz PCM is played back gaplessly and flushed on barge-in. Every drawing
 * question becomes an `ask_blueprint` tool call answered by AURA's own pipeline (`onAsk`).
 */

export type LiveStatus = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'error'

export interface LiveLine {
  role: 'user' | 'model' | 'tool'
  text: string
}

export interface LiveVoiceOptions {
  lang: Lang
  /** Runs one `ask_blueprint` call; resolves to the JSON the model receives. */
  onAsk(question: string): Promise<Record<string, unknown>>
}

const IN_RATE = 16_000
const OUT_RATE = 24_000

/** Bundled AudioWorklet (served from 'self', so it loads under the deploy CSP). */
const WORKLET_URL = new URL('./pcm-worklet.js', import.meta.url).href

function toB64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf)
  let s = ''
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(s)
}

function fromB64Pcm(b64: string): Float32Array {
  const bin = atob(b64)
  const n = bin.length >> 1
  const out = new Float32Array(n)
  for (let i = 0; i < n; i++) {
    let v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8)
    if (v >= 0x8000) v -= 0x10000
    out[i] = v / 0x8000
  }
  return out
}

interface ServerMsg {
  setupComplete?: unknown
  serverContent?: {
    modelTurn?: { parts?: { inlineData?: { data: string; mimeType?: string } }[] }
    inputTranscription?: { text?: string }
    outputTranscription?: { text?: string }
    interrupted?: boolean
    turnComplete?: boolean
  }
  toolCall?: { functionCalls?: { id?: string; name: string; args?: Record<string, unknown> }[] }
  toolCallCancellation?: { ids?: string[] }
  goAway?: unknown
}

export function useLiveVoiceSession({ lang, onAsk }: LiveVoiceOptions) {
  const [status, setStatus] = useState<LiveStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  const [lines, setLines] = useState<LiveLine[]>([])
  const ws = useRef<WebSocket | null>(null)
  const mic = useRef<MediaStream | null>(null)
  const inCtx = useRef<AudioContext | null>(null)
  const outCtx = useRef<AudioContext | null>(null)
  const playAt = useRef(0)
  const sources = useRef(new Set<AudioBufferSourceNode>())
  const pendingTools = useRef(0)
  const askRef = useRef(onAsk)
  const gen = useRef(0)
  useEffect(() => {
    askRef.current = onAsk
  })

  const append = useCallback((role: LiveLine['role'], text: string) => {
    if (!text) return
    setLines((ls) => {
      const last = ls[ls.length - 1]
      if (last && last.role === role && role !== 'tool') return [...ls.slice(0, -1), { role, text: last.text + text }]
      return [...ls.slice(-11), { role, text }]
    })
  }, [])

  const flush = useCallback(() => {
    sources.current.forEach((s) => {
      try {
        s.stop()
      } catch {
        /* already ended */
      }
    })
    sources.current.clear()
    playAt.current = 0
  }, [])

  const teardown = useCallback(() => {
    gen.current++
    flush()
    const sock = ws.current
    ws.current = null
    if (sock && sock.readyState <= WebSocket.OPEN) sock.close(1000)
    mic.current?.getTracks().forEach((t) => t.stop())
    mic.current = null
    void inCtx.current?.close().catch(() => {})
    void outCtx.current?.close().catch(() => {})
    inCtx.current = null
    outCtx.current = null
    pendingTools.current = 0
  }, [flush])

  const stop = useCallback(() => {
    teardown()
    setStatus('idle')
  }, [teardown])

  const fail = useCallback(
    (msg: string) => {
      teardown()
      setError(msg)
      setStatus('error')
    },
    [teardown],
  )

  const play = useCallback((pcm: Float32Array) => {
    const ctx = outCtx.current
    if (!ctx || !pcm.length) return
    const buf = ctx.createBuffer(1, pcm.length, OUT_RATE)
    buf.copyToChannel(pcm as Float32Array<ArrayBuffer>, 0)
    const src = ctx.createBufferSource()
    src.buffer = buf
    src.connect(ctx.destination)
    const at = Math.max(ctx.currentTime + 0.02, playAt.current)
    src.start(at)
    playAt.current = at + buf.duration
    sources.current.add(src)
    setStatus('speaking')
    src.onended = () => {
      sources.current.delete(src)
      if (!sources.current.size) setStatus((s) => (s === 'speaking' ? (pendingTools.current ? 'thinking' : 'listening') : s))
    }
  }, [])

  const start = useCallback(async () => {
    teardown()
    const my = gen.current
    setError(null)
    setLines([])
    setStatus('connecting')
    if (typeof WebSocket === 'undefined' || typeof AudioWorkletNode === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      fail(tu('vo_err_unsupported'))
      return
    }
    let media: MediaStream
    try {
      media = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      })
    } catch (e) {
      const name = (e as DOMException)?.name
      fail(name === 'NotAllowedError' || name === 'SecurityError' ? tu('vo_err_denied') : tu('vo_err_nomic'))
      return
    }
    if (my !== gen.current) return void media.getTracks().forEach((t) => t.stop())
    mic.current = media
    let token
    try {
      token = await mintLiveToken(lang)
    } catch (e) {
      if (my !== gen.current) return
      const limited = e instanceof VoiceApiError && e.status === 429
      const noApi = e instanceof VoiceApiError && e.code === 'no_api'
      fail(noApi ? tu('vo_err_no_api') : limited ? tu('vo_err_limited') : tu('vo_err_live_token'))
      return
    }
    if (my !== gen.current) return
    const AC = window.AudioContext
    const input = new AC()
    const output = new AC({ sampleRate: OUT_RATE })
    inCtx.current = input
    outCtx.current = output
    try {
      // Created after awaits: browsers may start them suspended, which would mean silent mic and speaker.
      await Promise.all([input.resume(), output.resume()])
      await input.audioWorklet.addModule(WORKLET_URL)
    } catch {
      fail(tu('vo_err_unsupported'))
      return
    }
    if (my !== gen.current) return
    const sock = new WebSocket(`${token.ws_url}?access_token=${encodeURIComponent(token.token)}`)
    sock.binaryType = 'arraybuffer'
    ws.current = sock
    const send = (o: unknown) => {
      if (sock.readyState === WebSocket.OPEN) sock.send(JSON.stringify(o))
    }

    sock.onopen = () => send({ setup: { model: `models/${token.model}` } })
    sock.onerror = () => {
      if (my === gen.current) fail(tu('vo_err_live_conn'))
    }
    sock.onclose = (e) => {
      if (my !== gen.current) return
      if (e.code === 1000) stop()
      else fail(e.reason ? `${tu('vo_err_live_conn')} (${e.reason.slice(0, 120)})` : tu('vo_err_live_conn'))
    }
    sock.onmessage = async (ev) => {
      if (my !== gen.current) return
      let msg: ServerMsg
      try {
        msg = JSON.parse(typeof ev.data === 'string' ? ev.data : new TextDecoder().decode(ev.data as ArrayBuffer))
      } catch {
        return
      }
      if (msg.setupComplete !== undefined) {
        const node = new AudioWorkletNode(input, 'aura-pcm')
        node.port.onmessage = (m: MessageEvent<ArrayBuffer>) =>
          send({ realtimeInput: { audio: { data: toB64(m.data), mimeType: `audio/pcm;rate=${IN_RATE}` } } })
        input.createMediaStreamSource(media).connect(node)
        setStatus('listening')
        return
      }
      const sc = msg.serverContent
      if (sc) {
        if (sc.interrupted) {
          flush()
          setStatus('listening')
        }
        for (const p of sc.modelTurn?.parts ?? []) if (p.inlineData?.data) play(fromB64Pcm(p.inlineData.data))
        if (sc.inputTranscription?.text) append('user', sc.inputTranscription.text)
        if (sc.outputTranscription?.text) append('model', sc.outputTranscription.text)
      }
      for (const fc of msg.toolCall?.functionCalls ?? []) {
        if (fc.name !== 'ask_blueprint') {
          send({ toolResponse: { functionResponses: [{ id: fc.id, name: fc.name, response: { error: 'unknown tool' } }] } })
          continue
        }
        const question = String(fc.args?.question ?? '')
        append('tool', question)
        pendingTools.current++
        setStatus('thinking')
        let result: Record<string, unknown>
        try {
          result = await askRef.current(question)
        } catch {
          result = { verified: false, error: 'AURA could not answer' }
        }
        pendingTools.current--
        if (my !== gen.current) return
        send({ toolResponse: { functionResponses: [{ id: fc.id, name: fc.name, response: result }] } })
      }
    }
  }, [append, fail, flush, lang, play, stop, teardown])

  useEffect(() => () => teardown(), [teardown])

  const active = status !== 'idle' && status !== 'error'
  return { status, error, lines, active, start: () => void start(), stop, toggle: () => (active ? stop() : void start()) }
}

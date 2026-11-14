import { useCallback, useSyncExternalStore } from 'react'
import type { AskResponse, Lang } from '@/api/contract'
import { tu } from '@/i18n/uiLang'
import { VoiceApiError, speak } from './voiceApi'

/**
 * Answer readout. One player for the whole page: starting one answer stops any other. The script is built on
 * the server from the answer's verified fields only (`POST /voice/speak`); audio is cached here per
 * (turn, locale, voice) and on the server per hash of the spoken fields, so a replay costs nothing.
 */

export type SpeakStatus = 'idle' | 'loading' | 'playing' | 'error'

interface PlayerState {
  id: string | null
  status: SpeakStatus
  error: string | null
  script: string | null
}

let state: PlayerState = { id: null, status: 'idle', error: null, script: null }
const listeners = new Set<() => void>()
const set = (next: Partial<PlayerState>) => {
  state = { ...state, ...next }
  listeners.forEach((fn) => fn())
}

// Played through Web Audio (not an <audio> element with a blob: URL) so the deploy CSP needs no media-src.
let ctx: AudioContext | null = null
let source: AudioBufferSourceNode | null = null
let abort: AbortController | null = null
const clips = new Map<string, { buffer: AudioBuffer; script: string }>()
const MAX_CLIPS = 24

function b64ToBytes(b64: string): ArrayBuffer {
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return bytes.buffer
}

function halt() {
  if (source) {
    source.onended = null
    try {
      source.stop()
    } catch {
      /* not started */
    }
    source = null
  }
}

export function stopSpeaking() {
  abort?.abort()
  abort = null
  halt()
  if (state.status !== 'idle' || state.id) set({ id: null, status: 'idle', error: null, script: null })
}

export async function speakAnswer(opts: {
  id: string
  response: AskResponse
  lang: Lang
  voice?: string
}): Promise<void> {
  stopSpeaking()
  // Created/resumed inside the click or key handler, before any await, so autoplay rules allow it.
  ctx ??= new AudioContext()
  const resumed = ctx.resume().catch(() => {})
  const voice = opts.voice ?? 'Kore'
  const key = `${opts.id}|${opts.lang}|${voice}`
  set({ id: opts.id, status: 'loading', error: null, script: null })
  let clip = clips.get(key)
  if (!clip) {
    const ctrl = new AbortController()
    abort = ctrl
    try {
      const r = await speak({ answerId: opts.response.answer_id, response: opts.response, lang: opts.lang, voice }, ctrl.signal)
      const buffer = await ctx.decodeAudioData(b64ToBytes(r.audio_b64))
      clip = { buffer, script: r.script }
      clips.set(key, clip)
      if (clips.size > MAX_CLIPS) clips.delete(clips.keys().next().value as string)
    } catch (e) {
      if (ctrl.signal.aborted) return
      const limited = e instanceof VoiceApiError && e.status === 429
      const noApi = e instanceof VoiceApiError && e.code === 'no_api'
      const api = e instanceof VoiceApiError
      set({
        status: 'error',
        error: noApi ? tu('vo_err_no_api') : limited ? tu('vo_err_limited') : api ? tu('vo_err_speak') : tu('vo_err_play'),
      })
      return
    }
    if (ctrl.signal.aborted || state.id !== opts.id) return
  }
  await resumed
  if (state.id !== opts.id) return
  const src = ctx.createBufferSource()
  src.buffer = clip.buffer
  src.connect(ctx.destination)
  src.onended = () => {
    if (source === src) {
      source = null
      set({ id: null, status: 'idle', script: null })
    }
  }
  source = src
  set({ status: 'playing', script: clip.script })
  try {
    src.start()
  } catch {
    if (source === src) set({ status: 'error', error: tu('vo_err_play') })
  }
}

const subscribe = (fn: () => void) => {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}
const snapshot = () => state

/** The shared player as seen from one answer card. */
export function useTextToSpeech(id: string, response: AskResponse | undefined, lang: Lang, voice?: string) {
  const s = useSyncExternalStore(subscribe, snapshot, snapshot)
  const mine = s.id === id
  const status: SpeakStatus = mine ? s.status : 'idle'
  const play = useCallback(() => {
    if (response) void speakAnswer({ id, response, lang, voice })
  }, [id, response, lang, voice])
  const stop = useCallback(() => {
    if (snapshot().id === id) stopSpeaking()
  }, [id])
  const toggle = useCallback(() => {
    if (snapshot().id === id && snapshot().status !== 'error') stop()
    else play()
  }, [id, play, stop])
  return { status, error: mine ? s.error : null, script: mine ? s.script : null, play, stop, toggle }
}

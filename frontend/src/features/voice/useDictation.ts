import { useCallback, useEffect, useRef, useState } from 'react'
import type { Lang } from '@/api/contract'
import { LOCALE } from '@/lib/format'
import { tu } from '@/i18n/uiLang'
import { useSpeech, type SpeechState } from './useSpeech'
import { VoiceApiError, transcribe, voiceApiAvailable } from './voiceApi'

/**
 * Dictation for the composer. MediaRecorder captures the clip and the backend (`POST /voice/transcribe`,
 * gemini-3.5-transcribe) returns the final text. The browser's Web Speech API, where present, only supplies
 * live interim text while recording; its errors never fail dictation. With no backend configured, the Web
 * Speech text is the result. Same return shape as `useSpeech`, so `VoiceCard` takes either.
 */

export type DictationState = SpeechState | 'transcribing'

const MAX_MS = 60_000

function recorderType(): string | undefined {
  if (typeof MediaRecorder === 'undefined') return undefined
  for (const t of ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']) {
    if (MediaRecorder.isTypeSupported?.(t)) return t
  }
  return undefined
}

export const dictationSupported = () =>
  typeof window !== 'undefined' &&
  typeof MediaRecorder !== 'undefined' &&
  Boolean(navigator.mediaDevices?.getUserMedia)

export function useDictation(lang: Lang) {
  const { text: interimText, start: liveStart, stop: liveStop, reset: liveReset } = useSpeech(LOCALE[lang])
  const [state, setState] = useState<DictationState>('idle')
  const [finalText, setFinalText] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const rec = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const chunks = useRef<Blob[]>([])
  const abort = useRef<AbortController | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const gen = useRef(0)
  const liveText = useRef('')
  useEffect(() => {
    liveText.current = interimText
  }, [interimText])

  const release = useCallback(() => {
    clearTimeout(timer.current)
    stream.current?.getTracks().forEach((t) => t.stop())
    stream.current = null
  }, [])

  const stop = useCallback(() => {
    liveStop()
    if (rec.current && rec.current.state !== 'inactive') rec.current.stop()
  }, [liveStop])

  const reset = useCallback(() => {
    gen.current++
    abort.current?.abort()
    if (rec.current && rec.current.state !== 'inactive') {
      rec.current.onstop = null
      rec.current.stop()
    }
    rec.current = null
    release()
    liveReset()
    setState('idle')
    setFinalText(null)
    setError(null)
  }, [liveReset, release])

  const start = useCallback(async () => {
    reset()
    const my = ++gen.current
    if (!dictationSupported()) {
      setError(tu('vo_err_unsupported'))
      setState('error')
      return
    }
    let media: MediaStream
    try {
      media = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
    } catch (e) {
      if (my !== gen.current) return
      const name = (e as DOMException)?.name
      setError(name === 'NotAllowedError' || name === 'SecurityError' ? tu('vo_err_denied') : tu('vo_err_nomic'))
      setState('error')
      return
    }
    if (my !== gen.current) {
      media.getTracks().forEach((t) => t.stop())
      return
    }
    stream.current = media
    const type = recorderType()
    const r = new MediaRecorder(media, type ? { mimeType: type } : undefined)
    chunks.current = []
    r.ondataavailable = (e) => {
      if (e.data.size) chunks.current.push(e.data)
    }
    r.onstop = async () => {
      release()
      if (my !== gen.current) return
      const clip = new Blob(chunks.current, { type: r.mimeType || type || 'audio/webm' })
      const fallback = liveText.current.trim()
      if (!voiceApiAvailable()) {
        setFinalText(fallback)
        setState(fallback ? 'done' : 'error')
        if (!fallback) setError(tu('vo_err_heard_nothing'))
        return
      }
      setState('transcribing')
      const ctrl = new AbortController()
      abort.current = ctrl
      try {
        const res = await transcribe(clip, lang, ctrl.signal)
        if (my !== gen.current) return
        const text = res.text.trim() || fallback
        setFinalText(text)
        if (text) setState('done')
        else {
          setError(tu('vo_err_heard_nothing'))
          setState('error')
        }
      } catch (e) {
        if (my !== gen.current || ctrl.signal.aborted) return
        if (fallback) {
          setFinalText(fallback)
          setState('done')
        } else {
          const limited = e instanceof VoiceApiError && e.status === 429
          setError(limited ? tu('vo_err_limited') : tu('vo_err_transcribe'))
          setState('error')
        }
      }
    }
    rec.current = r
    r.start(250)
    setState('listening')
    timer.current = setTimeout(() => stop(), MAX_MS)
    // Interim words while recording; optional.
    try {
      liveStart()
    } catch {
      /* no Web Speech: the backend transcript still arrives */
    }
  }, [lang, liveStart, release, reset, stop])

  useEffect(
    () => () => {
      gen.current++
      abort.current?.abort()
      if (rec.current && rec.current.state !== 'inactive') rec.current.stop()
      clearTimeout(timer.current)
      stream.current?.getTracks().forEach((t) => t.stop())
    },
    [],
  )

  const text = finalText ?? interimText
  return { state, text, error, start: () => void start(), stop, reset, interim: finalText === null }
}

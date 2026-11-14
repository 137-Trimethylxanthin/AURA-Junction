import { useCallback, useEffect, useRef, useState } from 'react'
import { tu } from '@/i18n/uiLang'

/** Minimal Web Speech API typing (not in lib.dom for all TS targets). */
interface SpeechAlt {
  transcript: string
}
interface SpeechResultLike {
  isFinal: boolean
  0: SpeechAlt
}
interface SpeechEventLike {
  resultIndex: number
  results: ArrayLike<SpeechResultLike>
}
interface Recognition {
  lang: string
  interimResults: boolean
  continuous: boolean
  onresult: ((e: SpeechEventLike) => void) | null
  onerror: ((e: { error: string }) => void) | null
  onend: (() => void) | null
  start(): void
  stop(): void
  abort(): void
}
type RecognitionCtor = new () => Recognition

function ctor(): RecognitionCtor | null {
  if (typeof window === 'undefined') return null
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

export const speechSupported = () => ctor() !== null

export type SpeechState = 'idle' | 'listening' | 'done' | 'error'

export function useSpeech(locale: string) {
  const [state, setState] = useState<SpeechState>('idle')
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const rec = useRef<Recognition | null>(null)

  const stop = useCallback(() => rec.current?.stop(), [])
  const reset = useCallback(() => {
    rec.current?.abort()
    rec.current = null
    setState('idle')
    setText('')
    setError(null)
  }, [])

  const start = useCallback(() => {
    const C = ctor()
    if (!C) return
    rec.current?.abort()
    const r = new C()
    r.lang = locale
    r.interimResults = true
    r.continuous = false
    let finalText = ''
    r.onresult = (e) => {
      let interim = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i]
        if (res.isFinal) finalText += res[0].transcript
        else interim += res[0].transcript
      }
      setText((finalText + interim).trim())
    }
    r.onerror = (e) => {
      setError(e.error === 'not-allowed' ? tu('vc_err_blocked') : tu('vc_err_stopped', { error: e.error }))
      setState('error')
    }
    r.onend = () => setState((s) => (s === 'listening' ? 'done' : s))
    rec.current = r
    setText('')
    setError(null)
    setState('listening')
    try {
      r.start()
    } catch {
      setState('error')
      setError(tu('vc_err_start'))
    }
  }, [locale])

  useEffect(() => () => rec.current?.abort(), [])

  return { state, text, error, start, stop, reset }
}

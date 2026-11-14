import type { AskResponse, Lang } from '@/api/contract'
import { presenterHeader } from '@/api/client'
import { apiBase } from '@/api/library'

/** Client for the backend voice router (backend/aura/voice.py). The Gemini key never reaches the browser. */

export class VoiceApiError extends Error {
  readonly code: string
  readonly status: number
  constructor(message: string, code: string, status: number) {
    super(message)
    this.code = code
    this.status = status
  }
}

export const voiceApiAvailable = () => apiBase() !== null

function url(path: string): string {
  const base = apiBase()
  if (!base) throw new VoiceApiError('No backend configured', 'no_api', 0)
  return base.replace(/\/+$/, '') + path
}

async function json<T>(res: Response): Promise<T> {
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    /* non-JSON error page */
  }
  if (!res.ok) {
    const b = (body ?? {}) as { error?: string; code?: string }
    throw new VoiceApiError(b.error ?? `HTTP ${res.status}`, b.code ?? 'internal', res.status)
  }
  return body as T
}

export interface TranscribeResult {
  text: string
  model: string
  lang: Lang
}

export async function transcribe(clip: Blob, lang: Lang, signal?: AbortSignal): Promise<TranscribeResult> {
  const type = (clip.type || 'audio/webm').split(';')[0]
  const res = await fetch(url(`/voice/transcribe?lang=${lang}`), {
    method: 'POST',
    headers: { 'content-type': type, ...presenterHeader() },
    body: clip,
    signal,
  })
  return json<TranscribeResult>(res)
}

export interface SpeakResult {
  script: string
  audio_b64: string
  mime_type: string
  voice: string
  lang: Lang
  model: string
  source: 'store' | 'client'
  cached: boolean
  key: string
}

export async function speak(
  req: { answerId?: string; response: AskResponse; lang: Lang; voice?: string },
  signal?: AbortSignal,
): Promise<SpeakResult> {
  const res = await fetch(url('/voice/speak'), {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...presenterHeader() },
    body: JSON.stringify({ answer_id: req.answerId, response: req.response, lang: req.lang, voice: req.voice ?? 'Kore' }),
    signal,
  })
  return json<SpeakResult>(res)
}

export interface LiveToken {
  token: string
  model: string
  ws_url: string
  lang: Lang
  expires_at: string
  new_session_expires_at: string
}

export async function mintLiveToken(lang: Lang, signal?: AbortSignal): Promise<LiveToken> {
  const res = await fetch(url(`/voice/token?lang=${lang}`), {
    method: 'POST',
    headers: { ...presenterHeader() },
    signal,
  })
  return json<LiveToken>(res)
}

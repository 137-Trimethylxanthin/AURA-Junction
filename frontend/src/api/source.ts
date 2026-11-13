import { ApiError, TransportError, getHealth, postAsk, type FetchLike } from './client'
import type { AskRequest, AskResponse } from './contract'
import { ask as askLocal } from './local'
import { BUNDLED_DRAWING_ID } from '@/data/drawing'

export type SourceKind = 'api' | 'local'
export type SourceState = 'waking' | 'ready'

export interface SourceStatus {
  kind: SourceKind
  state: SourceState
  /** Why the session is local although an API URL is configured. */
  reason?: 'no_api_url' | 'health_failed' | 'fell_back'
}

export interface AskResult {
  response: AskResponse
  /** True when this ask failed on the API and was answered locally; the UI shows a toast. */
  fellBack: boolean
}

export interface Source {
  status(): SourceStatus
  /** Probe /health once and fix the source for the session. Idempotent while running. */
  init(): Promise<SourceStatus>
  /** Re-probe /health on demand (e.g. a "Retry backend" button). */
  retry(): Promise<SourceStatus>
  ask(req: AskRequest): Promise<AskResult>
  subscribe(fn: (s: SourceStatus) => void): () => void
}

export interface SourceOptions {
  /** API base URL. Pass `null` or '' for no backend; `undefined` reads VITE_API_URL. */
  baseUrl?: string | null
  fetch?: FetchLike
  healthTimeoutMs?: number
  askTimeoutMs?: number
  local?: (req: AskRequest) => AskResponse
}

function envUrl(): string | null {
  const v = (import.meta.env?.VITE_API_URL as string | undefined) ?? ''
  return v.trim() || null
}

export function createSource(opts: SourceOptions = {}): Source {
  const baseUrl = opts.baseUrl === undefined ? envUrl() : opts.baseUrl || null
  const local = opts.local ?? askLocal
  const healthTimeoutMs = opts.healthTimeoutMs ?? 10_000
  const askTimeoutMs = opts.askTimeoutMs ?? 15_000
  const listeners = new Set<(s: SourceStatus) => void>()

  let status: SourceStatus = baseUrl
    ? { kind: 'local', state: 'waking' }
    : { kind: 'local', state: 'ready', reason: 'no_api_url' }
  let probing: Promise<SourceStatus> | null = null

  const set = (s: SourceStatus) => {
    status = s
    for (const fn of listeners) fn(s)
  }

  const probe = (): Promise<SourceStatus> => {
    if (!baseUrl) return Promise.resolve(status)
    if (probing) return probing
    set({ kind: status.kind, state: 'waking' })
    probing = getHealth({ baseUrl, fetch: opts.fetch }, healthTimeoutMs)
      .then(
        () => ({ kind: 'api', state: 'ready' }) as SourceStatus,
        () => ({ kind: 'local', state: 'ready', reason: 'health_failed' }) as SourceStatus,
      )
      .then((s) => {
        set(s)
        probing = null
        return s
      })
    return probing
  }

  let initialised = false
  const init = () => {
    if (initialised) return probing ?? Promise.resolve(status)
    initialised = true
    return probe()
  }
  return {
    status: () => status,
    init,
    retry: probe,
    subscribe(fn) {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    async ask(req) {
      await init()
      if (status.kind === 'local' || !baseUrl) return { response: local(req), fellBack: false }
      try {
        return { response: await postAsk({ baseUrl, fetch: opts.fetch }, req, askTimeoutMs), fellBack: false }
      } catch (e) {
        if (e instanceof ApiError) throw e
        if (e instanceof TransportError && req.drawing_id === BUNDLED_DRAWING_ID) {
          set({ kind: 'local', state: 'ready', reason: 'fell_back' })
          return { response: local(req), fellBack: true }
        }
        throw e
      }
    },
  }
}

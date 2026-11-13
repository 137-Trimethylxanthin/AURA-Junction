import { ApiError, TransportError, getDrawing, getDrawingStatus, getHealth, postAsk, type FetchLike } from './client'
import type { AskRequest, AskResponse, Drawing, DrawingStatusBody } from './contract'
import { ask as askLocal } from './local'
import { BUNDLED_DRAWING_ID } from '@/data/ids'

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
  /** True when an API URL is configured (drawings other than the bundled one need it). */
  hasApi(): boolean
  /** GET /drawings/:id (full drawing; 409 `processing` while not ready). */
  drawing(id: string): Promise<Drawing>
  /** GET /drawings/:id/status. */
  drawingStatus(id: string): Promise<DrawingStatusBody>
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
    hasApi: () => !!baseUrl,
    drawing(id) {
      if (!baseUrl) return Promise.reject(new TransportError('No API configured', 'network'))
      // Image URLs are root-relative to the API (contract §1.1); resolve them against the API base.
      const abs = (u: string) => (u.startsWith('/') && !u.startsWith('//') ? baseUrl.replace(/\/+$/, '') + u : u)
      return getDrawing({ baseUrl, fetch: opts.fetch }, id).then((d) => ({
        ...d,
        images: {
          ...d.images,
          rebuilt: abs(d.images.rebuilt),
          ...(d.images.original ? { original: abs(d.images.original) } : {}),
        },
        ...(d.thumbnail ? { thumbnail: abs(d.thumbnail) } : {}),
      }))
    },
    drawingStatus(id) {
      if (!baseUrl) return Promise.reject(new TransportError('No API configured', 'network'))
      return getDrawingStatus({ baseUrl, fetch: opts.fetch }, id)
    },
    subscribe(fn) {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    async ask(req) {
      await init()
      if (status.kind === 'local' || !baseUrl) {
        // Only the bundled drawing has local answers; any other drawing must never be answered from it.
        if (req.drawing_id !== BUNDLED_DRAWING_ID)
          throw new TransportError('This drawing needs the AURA server, which is not reachable right now', 'network')
        return { response: local(req), fellBack: false }
      }
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

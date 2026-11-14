import {
  ApiError,
  TransportError,
  deleteProject,
  getDrawing,
  getDrawingStatus,
  getHealth,
  listProjects,
  postAsk,
  saveProject,
  type FetchLike,
  type ProjectBundle,
  type ProjectCallOptions,
  type SavedProject,
} from './client'
import type {
  AnswererMode,
  AskRequest,
  AskResponse,
  ChatEvent,
  Drawing,
  DrawingStatusBody,
  Lang,
  Override,
  Scope,
} from './contract'
import { ChatUnavailable, getConversation, streamChat } from './stream'
import type { Conversation } from './contract'
import { safeUrl } from './contract'
import { ask as askLocal } from './local'
import { BUNDLED_DRAWING_ID } from '@/data/ids'
import { myProjects } from './library'

export type SourceKind = 'api' | 'local'
export type SourceState = 'waking' | 'ready'

export interface SourceStatus {
  kind: SourceKind
  state: SourceState
  /** Why the session is local although an API URL is configured. */
  reason?: 'no_api_url' | 'health_failed' | 'fell_back'
  /** Server answerer mode (`exact`, `hybrid`, or `agent`) from GET /health (1.2). */
  answerer?: AnswererMode
  /** Whether the agent sidecar was reachable when GET /health ran (1.2). */
  agentOk?: boolean
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
  /**
   * One chat turn. With the API: POST /chat, streaming events to `onEvent` (falls back to /ask when the server has
   * no /chat). Locally: the bundled answer, with no events. Rejects like `ask`, plus an AbortError on `signal`.
   */
  chat(req: ChatTurnRequest, onEvent: (e: ChatEvent) => void, signal?: AbortSignal): Promise<AskResult>
  /** GET /conversations/:id (API only; null when unknown or local). */
  conversation(id: string): Promise<Conversation | null>
  /** GET /projects (1.2; empty array when local or unconfigured). */
  projects?(call?: ProjectCallOptions): Promise<ProjectBundle[]>
  /** POST /projects (1.2; API only). */
  saveProject?(bundle: ProjectBundle, call?: ProjectCallOptions): Promise<SavedProject>
  /** DELETE /projects/:id (1.2; API only). */
  deleteProject?(id: string, call?: ProjectCallOptions): Promise<void>
}

export interface ChatTurnRequest {
  drawing_id: string
  question: string
  conversation_id?: string
  scope?: Scope
  project_id?: string
  overrides?: Override[]
  lang_hint?: Lang
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
  // The server's chat-turn deadline is 60 s; /ask can take as long on the agent route.
  const askTimeoutMs = opts.askTimeoutMs ?? 65_000
  const listeners = new Set<(s: SourceStatus) => void>()

  let status: SourceStatus = baseUrl
    ? { kind: 'local', state: 'waking' }
    : { kind: 'local', state: 'ready', reason: 'no_api_url' }
  let probing: Promise<SourceStatus> | null = null
  /** Set once the server shows it has no /chat; later turns go straight to /ask. */
  /** Until when questions go to /ask instead of /chat: for good after a 405 or a non-stream reply, 5 min after a 404. */
  let chatOffUntil = 0
  const CHAT_RETRY_MS = 5 * 60_000
  const RECOVER_MS = 3_000
  const RECOVER_TIMEOUT_MS = 1_500
  const RETRY_DELAY_MS = 600
  const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

  const set = (s: SourceStatus) => {
    status = s
    for (const fn of listeners) fn(s)
  }

  const apiStatus = (h: { answerer?: AnswererMode; agent_ok?: boolean }): SourceStatus => ({
    kind: 'api',
    state: 'ready',
    ...(h.answerer !== undefined ? { answerer: h.answerer } : {}),
    ...(h.agent_ok !== undefined ? { agentOk: h.agent_ok } : {}),
  })

  /**
   * A session demoted to local data (health failed, or a request failed) goes back to the API as soon as /health
   * answers again: the backend restarts often. Short probe, at most once per RECOVER_MS unless `force`.
   */
  let lastRecoverAt = 0
  const recover = async (force = false): Promise<void> => {
    if (!baseUrl || status.kind !== 'local' || !status.reason || status.reason === 'no_api_url') return
    if (probing) {
      await probing
      return
    }
    if (!force && Date.now() - lastRecoverAt < RECOVER_MS) return
    lastRecoverAt = Date.now()
    probing = getHealth({ baseUrl, fetch: opts.fetch }, RECOVER_TIMEOUT_MS)
      .then(
        (h) => set(apiStatus(h)),
        () => undefined,
      )
      .then(() => {
        probing = null
        return status
      })
    await probing
  }
  if (baseUrl && typeof window !== 'undefined') {
    const onWake = () => void recover(true)
    window.addEventListener('focus', onWake)
    window.addEventListener('online', onWake)
  }

  const probe = (): Promise<SourceStatus> => {
    if (!baseUrl) return Promise.resolve(status)
    if (probing) return probing
    set({ kind: status.kind, state: 'waking' })
    probing = getHealth({ baseUrl, fetch: opts.fetch }, healthTimeoutMs)
      .then(
        (h) => apiStatus(h),
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
      const abs = (u: string) => {
        const s = safeUrl(u) ?? ''
        return s.startsWith('/') ? baseUrl.replace(/\/+$/, '') + s : s
      }
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
    async conversation(id) {
      await init()
      if (status.kind !== 'api' || !baseUrl) return null
      return getConversation({ baseUrl, fetch: opts.fetch }, id)
    },
    async projects(call) {
      await init()
      if (status.kind !== 'api' || !baseUrl) return []
      try {
        // Ownership credentials, so bundles over this browser's uploads are listed too.
        return await listProjects(
          { baseUrl, fetch: opts.fetch },
          { ...call, headers: { ...myProjects.listHeaders(), ...call?.headers } },
        )
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return []
        throw e
      }
    },
    async saveProject(bundle, call) {
      await init()
      if (!baseUrl) throw new TransportError('No API configured', 'network')
      return saveProject({ baseUrl, fetch: opts.fetch }, bundle, call)
    },
    async deleteProject(id, call) {
      await init()
      if (!baseUrl) throw new TransportError('No API configured', 'network')
      await deleteProject({ baseUrl, fetch: opts.fetch }, id, call)
    },
    async chat(turn, onEvent, signal) {
      await init()
      await recover()
      const targetProjId = turn.scope?.kind === 'project' ? turn.scope.project_id : turn.project_id
      const syncLocalProject = async (pid: string | undefined): Promise<boolean> => {
        if (!baseUrl || !pid) return false
        const localBundle = myProjects.get(pid)
        if (!localBundle || localBundle.floors.length < 2) return false
        try {
          const saved = await saveProject(
            { baseUrl, fetch: opts.fetch },
            localBundle,
            { headers: myProjects.authHeaders(localBundle) },
          )
          if (saved.token) myProjects.setToken(localBundle.id, saved.token)
          return true
        } catch {
          return false
        }
      }
      const askReq: AskRequest = {
        drawing_id: turn.drawing_id,
        question: turn.question,
        ...(turn.scope ? { scope: turn.scope } : {}),
        ...(turn.project_id ? { project_id: turn.project_id } : {}),
        ...(turn.overrides ? { overrides: turn.overrides } : {}),
        ...(turn.lang_hint ? { lang_hint: turn.lang_hint } : {}),
      }
      if (status.kind === 'local' || !baseUrl || Date.now() < chatOffUntil) return this.ask(askReq)
      if (targetProjId) {
        const localBundle = myProjects.get(targetProjId)
        if (localBundle && !localBundle.auto_matched && !myProjects.tokenOf(targetProjId)) {
          await syncLocalProject(targetProjId)
        }
      }
      let response: AskResponse | undefined
      let seen = false
      let retried = false
      let syncedOn404 = false
      for (;;) {
        try {
          await streamChat(
            {
              drawing_id: turn.drawing_id,
              message: turn.question,
              ...(turn.conversation_id ? { conversation_id: turn.conversation_id } : {}),
              ...(turn.scope ? { scope: turn.scope } : {}),
              ...(turn.project_id ? { project_id: turn.project_id } : {}),
              ...(turn.overrides ? { overrides: turn.overrides } : {}),
              ...(turn.lang_hint ? { lang_hint: turn.lang_hint } : {}),
            },
            {
              baseUrl,
              fetch: opts.fetch,
              signal,
              headers: myProjects.listHeaders(),
              onEvent(e) {
                seen = true
                if (e.event === 'answer') response = e.data.response
                if (e.event === 'error') throw new ApiError(e.data.error, 200, e.data.code, e.data)
                onEvent(e)
              },
            },
          )
          break
        } catch (e) {
          // Local project created offline or before server sync settled: push it to /projects and retry once.
          if (
            e instanceof ApiError &&
            e.status === 404 &&
            e.code === 'not_found' &&
            targetProjId &&
            !seen &&
            !signal?.aborted &&
            !syncedOn404 &&
            (await syncLocalProject(targetProjId))
          ) {
            syncedOn404 = true
            continue
          }
          // A transient network error before anything streamed: the backend may just be restarting. Retry once.
          if (e instanceof TransportError && e.kind !== 'timeout' && !seen && !signal?.aborted && !retried) {
            retried = true
            await sleep(RETRY_DELAY_MS)
            continue
          }
          // The stream broke after a complete answer arrived (before `done`): the answer stands.
          if (response && !(e instanceof ApiError)) return { response, fellBack: false }
          if (e instanceof ChatUnavailable) {
            chatOffUntil = e.transient ? Date.now() + CHAT_RETRY_MS : Infinity
            return this.ask(askReq)
          }
          if (
            e instanceof TransportError &&
            e.kind !== 'timeout' &&
            !seen &&
            turn.drawing_id === BUNDLED_DRAWING_ID &&
            !signal?.aborted
          ) {
            lastRecoverAt = Date.now()
            set({ kind: 'local', state: 'ready', reason: 'fell_back' })
            return { response: local(askReq), fellBack: true }
          }
          throw e
        }
      }
      if (!response) throw new ApiError('The answer stream ended without an answer', 200, 'invalid_response')
      return { response, fellBack: false }
    },
    async ask(req) {
      await init()
      await recover()
      if (status.kind === 'local' || !baseUrl) {
        // Only the bundled drawing has local answers; any other drawing must never be answered from it.
        if (req.drawing_id !== BUNDLED_DRAWING_ID)
          throw new TransportError('This drawing needs the AURA server, which is not reachable right now', 'network')
        return { response: local(req), fellBack: false }
      }
      const targetProjId = req.scope?.kind === 'project' ? req.scope.project_id : req.project_id
      const syncLocalProject = async (pid: string | undefined): Promise<boolean> => {
        if (!baseUrl || !pid) return false
        const localBundle = myProjects.get(pid)
        if (!localBundle || localBundle.floors.length < 2) return false
        try {
          const saved = await saveProject(
            { baseUrl, fetch: opts.fetch },
            localBundle,
            { headers: myProjects.authHeaders(localBundle) },
          )
          if (saved.token) myProjects.setToken(localBundle.id, saved.token)
          return true
        } catch {
          return false
        }
      }
      if (targetProjId) {
        const localBundle = myProjects.get(targetProjId)
        if (localBundle && !localBundle.auto_matched && !myProjects.tokenOf(targetProjId)) {
          await syncLocalProject(targetProjId)
        }
      }
      const callAsk = () =>
        postAsk({ baseUrl, fetch: opts.fetch }, req, { timeoutMs: askTimeoutMs, headers: myProjects.listHeaders() })
      try {
        try {
          return { response: await callAsk(), fellBack: false }
        } catch (e) {
          if (
            e instanceof ApiError &&
            e.status === 404 &&
            e.code === 'not_found' &&
            targetProjId &&
            (await syncLocalProject(targetProjId))
          ) {
            return { response: await callAsk(), fellBack: false }
          }
          // A transient network error: the backend may just be restarting. Retry once before giving up.
          if (!(e instanceof TransportError) || e.kind === 'timeout') throw e
          await sleep(RETRY_DELAY_MS)
          return { response: await callAsk(), fellBack: false }
        }
      } catch (e) {
        if (e instanceof ApiError) throw e
        // A slow answer is not a dead server: only network errors and 5xx demote the session to local data.
        if (e instanceof TransportError && e.kind !== 'timeout' && req.drawing_id === BUNDLED_DRAWING_ID) {
          lastRecoverAt = Date.now()
          set({ kind: 'local', state: 'ready', reason: 'fell_back' })
          return { response: local(req), fellBack: true }
        }
        throw e
      }
    },
  }
}

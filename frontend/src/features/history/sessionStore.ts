import { createStore, type StoreApi } from 'zustand/vanilla'
import type { AskResponse, Lang } from '@/api/contract'
import { valueParts } from '@/features/chat/view'
import { plural, tu, uiLang } from '@/i18n/uiLang'
import { deriveAnswer } from '@/lib/answer'
import { CONVERSATION_KEY, type AgentTurn, type AppState, type AppStore, type Turn, type UserTurn } from '@/store/appStore'

/**
 * Session history: every conversation is kept in this browser (`aura.sessions.v1`) and mirrored, best effort,
 * to the backend (`/sessions`). The thread on screen is saved live as the "current" session; clearing it,
 * starting a new conversation or opening another drawing detaches it, so nothing is discarded.
 */

export const SESSIONS_KEY = 'aura.sessions.v1'
const CLIENT_KEY = 'aura.sessions.client'
/** Header carrying the per-browser client id to `/sessions` (needs `x-aura-client` in the backend's CORS allow_headers). */
export const CLIENT_HEADER = 'X-Aura-Client'
const CLIENT_ID_RE = /^[0-9a-f]{32}$/
/** Hard cap on stored sessions (pinned ones are evicted last). */
export const MAX_SESSIONS = 40
/** Soft byte budget for the serialized archive (localStorage is ~5 MB per origin, shared with the app). */
export const MAX_BYTES = 2_500_000
export const OPEN_HISTORY_EVENT = 'aura:open-history'
const SAVE_DEBOUNCE_MS = 600

export interface StoredSession {
  id: string
  title: string
  /** True once the user renamed it: the automatic title stops following the thread. */
  customTitle: boolean
  drawing_id: string
  drawing_title?: string
  floor_label?: string | null
  project_id: string | null
  project_title?: string | null
  turns: Turn[]
  activeTurnId: string | null
  /** Rejected evidence box ids of the active answer (the per-turn toggles stay the source of truth). */
  rejectedBoxIds: string[]
  conversation_id: string | null
  pinned: boolean
  created_at: number
  updated_at: number
}

export type SessionFilter = 'here' | 'all'
export type SessionGroupKey = 'pinned' | 'today' | 'yesterday' | 'earlier'
export interface SessionGroup {
  key: SessionGroupKey
  sessions: StoredSession[]
}

export interface SessionHistoryState {
  sessions: StoredSession[]
  /** The archived session the thread on screen belongs to (null: not saved yet, or empty). */
  currentId: string | null
  open: boolean
  /** When the sheet was last opened (groups Today / Yesterday against it). */
  openedAt: number
  /** Id of the session being restored (navigation in progress). */
  restoring: string | null
}

export interface SessionStoreDeps {
  app: AppStore
  storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null
  now?: () => number
  /** Backend base URL for the mirror; null/undefined turns the mirror off. */
  remoteBase?: string | null
  fetchImpl?: typeof fetch
}

const defaultStorage = () => {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

const newId = () => `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`

const clip = (s: string, n: number) => {
  const one = s.replace(/\s+/g, ' ').trim()
  return one.length > n ? `${one.slice(0, n - 1).trimEnd()}…` : one
}

/** The headline of an answer, e.g. "4 windows", "86.5 m²", or "no answer". */
export function headlineOf(resp: AskResponse | undefined, lang: Lang = uiLang.get()): string | null {
  if (!resp) return null
  if (resp.answer.kind === 'abstain') return tu('hist_no_answer')
  try {
    const parts = valueParts(resp, deriveAnswer(resp, { rejected: {}, included: {} }), lang)
    return parts ? clip(parts.filter(Boolean).join(' '), 32) : null
  } catch {
    return null
  }
}

const questions = (turns: Turn[]) => turns.filter((x): x is UserTurn => x.role === 'user')
const answers = (turns: Turn[]) => turns.filter((x): x is AgentTurn => x.role === 'agent')

/** Automatic title: first question + headline of its answer ("North windows · 4 windows"). */
export function autoTitle(turns: Turn[]): string {
  const q = questions(turns)[0]
  if (!q) return tu('hist_untitled')
  const a = answers(turns).find((x) => x.replyTo === q.id)
  const head = headlineOf(a?.response)
  return head ? `${clip(q.text, 48)} · ${head}` : clip(q.text, 64)
}

export const questionCount = (s: Pick<StoredSession, 'turns'>) => questions(s.turns).length

/**
 * Turns worth keeping: no answer still pending (and not its question either), ids rewritten to
 * `<session>-t<n>` so they never collide with the store's `u-N`/`a-N` counter after a reload.
 */
export function snapshotTurns(turns: Turn[], sessionId: string): { turns: Turn[]; idMap: Map<string, string> } {
  const pendingReplies = new Set(
    turns.filter((x) => x.role === 'agent' && x.status === 'pending').map((x) => (x as AgentTurn).replyTo),
  )
  const kept = turns.filter((x) => (x.role === 'agent' ? x.status !== 'pending' : !pendingReplies.has(x.id)))
  const idMap = new Map<string, string>()
  kept.forEach((x, i) => idMap.set(x.id, `${sessionId}-t${i}`))
  const out = kept.map((x): Turn => {
    const id = idMap.get(x.id)!
    if (x.role === 'user') return { ...x, id }
    // The stream's evidence repeats the answer's; keep the step list, drop the copy.
    const stream = x.stream && x.response ? { ...x.stream, evidence: { boxes: [], rooms: [] } } : x.stream
    return { ...x, id, replyTo: idMap.get(x.replyTo) ?? x.replyTo, ...(stream ? { stream } : {}) }
  })
  return { turns: out, idMap }
}

const projectIdOf = (s: AppState): string | null =>
  s.activeProject && s.activeProject.floors.some((f) => f.drawing_id === s.session.drawing.id) ? s.activeProject.id : null

const dayStart = (t: number) => {
  const d = new Date(t)
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

/** Pinned first, then Today / Yesterday / Earlier, newest first inside each group. */
export function groupSessions(sessions: StoredSession[], now: number): SessionGroup[] {
  const today = dayStart(now)
  const yesterday = today - 86_400_000
  const groups: Record<SessionGroupKey, StoredSession[]> = { pinned: [], today: [], yesterday: [], earlier: [] }
  for (const s of [...sessions].sort((a, b) => b.updated_at - a.updated_at)) {
    const key: SessionGroupKey = s.pinned
      ? 'pinned'
      : s.updated_at >= today
        ? 'today'
        : s.updated_at >= yesterday
          ? 'yesterday'
          : 'earlier'
    groups[key].push(s)
  }
  return (['pinned', 'today', 'yesterday', 'earlier'] as const)
    .map((key) => ({ key, sessions: groups[key] }))
    .filter((g) => g.sessions.length > 0)
}

/** 'here': sessions of the open project (any floor), or of the open drawing when it has no project. */
export function filterSessions(
  sessions: StoredSession[],
  filter: SessionFilter,
  ctx: { drawingId: string; projectId: string | null },
  query = '',
): StoredSession[] {
  const q = query.trim().toLowerCase()
  return sessions.filter((s) => {
    if (filter === 'here') {
      const here = ctx.projectId ? s.project_id === ctx.projectId || s.drawing_id === ctx.drawingId : s.drawing_id === ctx.drawingId
      if (!here) return false
    }
    if (!q) return true
    return (
      s.title.toLowerCase().includes(q) ||
      (s.drawing_title ?? '').toLowerCase().includes(q) ||
      questions(s.turns).some((x) => x.text.toLowerCase().includes(q))
    )
  })
}

function isSession(x: unknown): x is StoredSession {
  const s = x as StoredSession
  return !!s && typeof s.id === 'string' && typeof s.drawing_id === 'string' && Array.isArray(s.turns)
}

export type SessionStore = ReturnType<typeof createSessionStore>

export function createSessionStore(deps: SessionStoreDeps) {
  const { app } = deps
  const storage = deps.storage === undefined ? defaultStorage() : deps.storage
  const now = deps.now ?? (() => Date.now())
  const remoteBase = deps.remoteBase?.replace(/\/+$/, '') || null
  const doFetch = deps.fetchImpl ?? (typeof fetch === 'function' ? fetch.bind(globalThis) : undefined)

  const load = (): StoredSession[] => {
    try {
      const raw = storage?.getItem(SESSIONS_KEY)
      const parsed: unknown = raw ? JSON.parse(raw) : []
      return Array.isArray(parsed) ? parsed.filter(isSession) : []
    } catch {
      return []
    }
  }

  const state: StoreApi<SessionHistoryState> = createStore<SessionHistoryState>()(() => ({
    sessions: load(),
    currentId: null,
    open: false,
    openedAt: now(),
    restoring: null,
  }))

  // ---- persistence -----------------------------------------------------------------------------------
  /** Oldest unpinned first, then oldest pinned: the order sessions are dropped in when over budget. */
  const evictionOrder = (list: StoredSession[]) =>
    [...list].sort((a, b) => Number(a.pinned) - Number(b.pinned) || a.updated_at - b.updated_at)

  /** Apply the count cap, then write; on quota errors drop the oldest sessions until it fits. */
  const persist = (list: StoredSession[]): StoredSession[] => {
    let kept = list
    if (kept.length > MAX_SESSIONS) {
      const drop = new Set(evictionOrder(kept).slice(0, kept.length - MAX_SESSIONS).map((s) => s.id))
      kept = kept.filter((s) => !drop.has(s.id))
    }
    if (!storage) return kept
    for (;;) {
      const json = JSON.stringify(kept)
      if (json.length <= MAX_BYTES || kept.length <= 1) {
        try {
          storage.setItem(SESSIONS_KEY, json)
          return kept
        } catch {
          if (kept.length <= 1) return kept // storage full or blocked: this page keeps it in memory
        }
      }
      const victim = evictionOrder(kept).find((s) => s.id !== state.getState().currentId) ?? evictionOrder(kept)[0]
      kept = kept.filter((s) => s.id !== victim.id)
    }
  }

  const commit = (list: StoredSession[]) => state.setState({ sessions: persist(list) })

  // ---- backend mirror (best effort; local stays the source of truth) ----------------------------------
  /**
   * The per-browser key that scopes this archive on the backend: 128 random bits from the CSPRNG, sent
   * only in the `X-Aura-Client` header (never in a URL). Without crypto or storage the mirror stays off.
   */
  const clientId = ((): string | null => {
    try {
      const have = storage?.getItem(CLIENT_KEY)
      if (have && CLIENT_ID_RE.test(have)) return have
      const bytes = globalThis.crypto?.getRandomValues?.(new Uint8Array(16))
      if (!bytes || !storage) return null
      const id = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
      storage.setItem(CLIENT_KEY, id)
      return id
    } catch {
      return null
    }
  })()
  let remoteDown = false
  const remoteOn = () => Boolean(remoteBase && doFetch && clientId && !remoteDown)
  const remote = (method: 'POST' | 'DELETE', path: string, body?: unknown) => {
    if (!remoteOn()) return
    void doFetch!(`${remoteBase}${path}`, {
      method,
      headers: {
        [CLIENT_HEADER]: clientId!,
        ...(body ? { 'content-type': 'application/json' } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    })
      .then((r) => {
        // No such endpoint on this backend: stop trying for this page.
        if (r.status === 404 || r.status === 405) remoteDown = true
      })
      .catch(() => {})
  }
  const pushRemote = (s: StoredSession) => remote('POST', '/sessions', s)
  const deleteRemote = (id: string) => remote('DELETE', `/sessions/${encodeURIComponent(id)}`)

  /** Merge sessions the backend has that this browser lost (cleared storage). */
  const pullRemote = async () => {
    if (!remoteOn()) return
    try {
      const r = await doFetch!(`${remoteBase}/sessions`, { headers: { [CLIENT_HEADER]: clientId! } })
      if (!r.ok) {
        if (r.status === 404 || r.status === 405) remoteDown = true
        return
      }
      const body = (await r.json()) as { sessions?: unknown[] }
      const incoming = (body.sessions ?? []).filter(isSession)
      const have = new Set(state.getState().sessions.map((s) => s.id))
      const fresh = incoming.filter((s) => !have.has(s.id))
      if (fresh.length) commit([...state.getState().sessions, ...fresh])
    } catch {
      /* offline or no backend: local history only */
    }
  }

  // ---- snapshots ---------------------------------------------------------------------------------------
  const sessionFrom = (s: AppState, id: string, prev?: StoredSession): StoredSession | null => {
    const { turns, idMap } = snapshotTurns(s.turns, id)
    if (!questions(turns).length) return null
    const active = s.ui.activeTurnId ? idMap.get(s.ui.activeTurnId) ?? null : null
    const activeTurn = answers(turns).find((x) => x.id === active)
    const projectId = projectIdOf(s)
    const floor = s.activeProject?.floors.find((f) => f.drawing_id === s.session.drawing.id)?.label
    const t = now()
    return {
      id,
      title: prev?.customTitle ? prev.title : autoTitle(turns),
      customTitle: prev?.customTitle ?? false,
      drawing_id: s.session.drawing.id,
      drawing_title: s.session.drawing.title,
      floor_label: floor ?? (typeof s.session.drawing.floor_label === 'string' ? s.session.drawing.floor_label : null),
      project_id: projectId,
      project_title: projectId ? s.activeProject?.title ?? null : null,
      turns,
      activeTurnId: active,
      rejectedBoxIds: activeTurn ? Object.keys(activeTurn.toggles.rejected).filter((k) => activeTurn.toggles.rejected[k]) : [],
      conversation_id: s.session.conversationId,
      pinned: prev?.pinned ?? false,
      created_at: prev?.created_at ?? t,
      updated_at: t,
    }
  }

  /** Upsert the thread `s` under `id`; keeps `updated_at` when nothing but the save time would change. */
  const upsert = (s: AppState, id: string): StoredSession | null => {
    const list = state.getState().sessions
    const prev = list.find((x) => x.id === id)
    const next = sessionFrom(s, id, prev)
    if (!next) return null
    if (
      prev &&
      JSON.stringify(prev.turns) === JSON.stringify(next.turns) &&
      prev.activeTurnId === next.activeTurnId &&
      prev.conversation_id === next.conversation_id
    ) {
      return prev
    }
    commit(prev ? list.map((x) => (x.id === id ? next : x)) : [next, ...list])
    pushRemote(next)
    return next
  }

  /** Save the thread on screen (live autosave); returns its session id, or null when there is nothing to keep. */
  const saveCurrent = (): string | null => {
    const s = app.getState()
    if (suppressed && s.turns[0] === suppressed.first && questions(s.turns).length <= suppressed.count) return null
    suppressed = null
    const target = targetFor(s)
    if (!target) return null
    if (target.skip) {
      if (state.getState().currentId !== target.id) state.setState({ currentId: target.id })
      return target.id
    }
    const saved = upsert(s, target.id)
    if (!saved) return null
    if (state.getState().currentId !== target.id) state.setState({ currentId: target.id })
    return target.id
  }

  /**
   * Which session the thread `s` belongs to. With no current session, a thread the server gave back
   * (reload, or back on a drawing) is matched by conversation id; `skip` when that server copy adds no
   * question, since it has none of the saved rejects and would overwrite them.
   */
  const targetFor = (s: AppState, known?: string | null): { id: string; skip: boolean } | null => {
    const cur = state.getState().currentId ?? known ?? null
    if (cur) return { id: cur, skip: false }
    const conv = s.session.conversationId
    const match = conv ? state.getState().sessions.find((x) => x.conversation_id === conv) : undefined
    if (!match) return { id: newId(), skip: false }
    return { id: match.id, skip: questions(s.turns).length <= questionCount(match) }
  }

  /** Set when the session on screen is deleted: the same thread is not saved again until it grows. */
  let suppressed: { first: Turn | undefined; count: number } | null = null

  /**
   * Archive the thread on screen and detach it: the next question starts a new session.
   * Call before `newConversation()`; returns the session id (or null when the thread was empty).
   */
  const archiveCurrent = (): string | null => {
    const id = saveCurrent()
    lastDetached = id ? { id, first: app.getState().turns[0] } : null
    state.setState({ currentId: null })
    return id
  }

  // ---- restore -----------------------------------------------------------------------------------------
  const apply = (session: StoredSession) => {
    const s = app.getState()
    s.cancel()
    app.setState((st) => ({
      turns: session.turns,
      undoStack: [],
      focusedGroupId: null,
      session: { ...st.session, conversationId: session.conversation_id },
      ui: {
        ...st.ui,
        activeTurnId: session.activeTurnId,
        selectedEvidenceId: null,
        hoveredEvidenceIds: [],
        focusedGroupId: null,
        marks: [],
        attachedMarkId: null,
      },
    }))
    try {
      const key = CONVERSATION_KEY(session.drawing_id, projectIdOf(app.getState()))
      if (session.conversation_id) storage?.setItem(key, session.conversation_id)
      else storage?.removeItem(key)
    } catch {
      /* storage blocked */
    }
    state.setState({ currentId: session.id, restoring: null })
  }

  let cancelPending: (() => void) | null = null

  /**
   * Bring a session back: saves the thread on screen first, opens the session's drawing (via `navigate`,
   * e.g. react-router's) and, once that drawing is ready, puts its turns and active answer on the plan.
   */
  const restore = (id: string, navigate?: (path: string) => void, timeoutMs = 60_000): Promise<boolean> => {
    const session = state.getState().sessions.find((x) => x.id === id)
    if (!session) return Promise.resolve(false)
    cancelPending?.()
    if (state.getState().currentId !== id) archiveCurrent()
    const ready = (s: AppState) =>
      s.session.drawing.id === session.drawing_id && s.session.load.id === session.drawing_id && s.session.load.state === 'ready'
    if (ready(app.getState())) {
      apply(session)
      return Promise.resolve(true)
    }
    state.setState({ restoring: id })
    return new Promise<boolean>((resolve) => {
      const done = (ok: boolean) => {
        unsub()
        clearTimeout(timer)
        cancelPending = null
        if (ok) apply(session)
        else if (state.getState().restoring === id) state.setState({ restoring: null })
        resolve(ok)
      }
      const unsub = app.subscribe((s) => {
        if (ready(s)) return done(true)
        if (s.session.load.id === session.drawing_id && !['loading', 'processing', 'ready'].includes(s.session.load.state))
          done(false)
      })
      const timer = setTimeout(() => done(false), timeoutMs)
      cancelPending = () => done(false)
      if (navigate) navigate(`/d/${encodeURIComponent(session.drawing_id)}`)
      else void app.getState().loadDrawing(session.drawing_id)
    })
  }

  // ---- edits -------------------------------------------------------------------------------------------
  const patch = (id: string, fn: (s: StoredSession) => StoredSession) => {
    const list = state.getState().sessions
    const target = list.find((x) => x.id === id)
    if (!target) return
    const next = fn(target)
    commit(list.map((x) => (x.id === id ? next : x)))
    pushRemote(next)
  }
  const rename = (id: string, title: string) => {
    const clean = title.replace(/\s+/g, ' ').trim().slice(0, 120)
    patch(id, (s) => (clean ? { ...s, title: clean, customTitle: true } : { ...s, title: autoTitle(s.turns), customTitle: false }))
  }
  const setPinned = (id: string, pinned: boolean) => patch(id, (s) => ({ ...s, pinned }))
  const togglePin = (id: string) => patch(id, (s) => ({ ...s, pinned: !s.pinned }))

  /** Delete a session; returns an undo callback that puts it back where it was. */
  const remove = (id: string): (() => void) => {
    const list = state.getState().sessions
    const idx = list.findIndex((x) => x.id === id)
    if (idx === -1) return () => {}
    const removed = list[idx]
    commit(list.filter((x) => x.id !== id))
    const wasCurrent = state.getState().currentId === id
    if (wasCurrent) {
      // The thread stays on screen; do not save it straight back under a new id.
      const turns = app.getState().turns
      suppressed = { first: turns[0], count: questions(turns).length }
      state.setState({ currentId: null })
    }
    if (lastDetached?.id === id) lastDetached = null
    deleteRemote(id)
    return () => {
      const cur = state.getState().sessions
      if (cur.some((x) => x.id === id)) return
      const next = [...cur]
      next.splice(Math.min(idx, next.length), 0, removed)
      commit(next)
      pushRemote(removed)
      if (wasCurrent && suppressed && app.getState().turns[0] === suppressed.first) {
        suppressed = null
        state.setState({ currentId: id })
      }
    }
  }

  // ---- live sync with the app store ---------------------------------------------------------------------
  /** The last thread that went off screen, so an Undo of "New conversation" re-attaches to the same session. */
  let lastDetached: { id: string; first: Turn | undefined } | null = null
  let saveTimer: ReturnType<typeof setTimeout> | undefined
  let stopSync: (() => void) | null = null
  const setOpen = (open: boolean) => state.setState(open ? { open, openedAt: now() } : { open })
  const onOpenEvent = () => setOpen(true)

  const startSync = () => {
    if (stopSync) return stopSync
    const unsub = app.subscribe((s, prev) => {
      if (s.turns === prev.turns && s.ui.activeTurnId === prev.ui.activeTurnId) return
      if (prev.turns.length > 0 && s.turns.length === 0) {
        // Cleared, new conversation, or another drawing: keep what was there, then detach.
        clearTimeout(saveTimer)
        // Already archived by archiveCurrent() (restore, or the lead's "New conversation"): same session.
        const known = lastDetached && prev.turns[0] === lastDetached.first ? lastDetached.id : null
        const isSuppressed = suppressed && prev.turns[0] === suppressed.first && questions(prev.turns).length <= suppressed.count
        const target = isSuppressed ? null : targetFor(prev, known)
        const saved = target ? (target.skip ? true : upsert(prev, target.id)) : null
        lastDetached = target && saved ? { id: target.id, first: prev.turns[0] } : null
        suppressed = null
        state.setState({ currentId: null })
        return
      }
      if (prev.turns.length === 0 && s.turns.length > 0 && lastDetached && s.turns[0] === lastDetached.first) {
        state.setState({ currentId: lastDetached.id }) // Undo brought the same thread back
        lastDetached = null
      }
      clearTimeout(saveTimer)
      saveTimer = setTimeout(saveCurrent, SAVE_DEBOUNCE_MS)
    })
    const flush = () => {
      clearTimeout(saveTimer)
      saveCurrent()
    }
    const win = typeof window !== 'undefined' ? window : null
    win?.addEventListener(OPEN_HISTORY_EVENT, onOpenEvent)
    win?.addEventListener('pagehide', flush)
    void pullRemote()
    stopSync = () => {
      unsub()
      clearTimeout(saveTimer)
      win?.removeEventListener(OPEN_HISTORY_EVENT, onOpenEvent)
      win?.removeEventListener('pagehide', flush)
      stopSync = null
    }
    return stopSync
  }

  return {
    store: state,
    clientId,
    startSync,
    saveCurrent,
    archiveCurrent,
    restore,
    rename,
    setPinned,
    togglePin,
    remove,
    pullRemote,
    setOpen,
    get: (id: string) => state.getState().sessions.find((x) => x.id === id),
    list: (filter: SessionFilter = 'all', query = '') => {
      const s = app.getState()
      return filterSessions(state.getState().sessions, filter, { drawingId: s.session.drawing.id, projectId: projectIdOf(s) }, query)
    },
    grouped: (filter: SessionFilter = 'all', query = '') => {
      const s = app.getState()
      return groupSessions(
        filterSessions(state.getState().sessions, filter, { drawingId: s.session.drawing.id, projectId: projectIdOf(s) }, query),
        now(),
      )
    },
  }
}

// ---- export ------------------------------------------------------------------------------------------------
const csvCell = (v: string | number | undefined | null) => {
  const s = v === undefined || v === null ? '' : typeof v === 'string' && /^[=+\-@\t\r]/.test(v) ? `'${v}` : String(v)
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

const pairs = (s: StoredSession) =>
  questions(s.turns).map((q) => ({ q, a: answers(s.turns).find((x) => x.replyTo === q.id) }))

const answerText = (a: AgentTurn | undefined) =>
  a?.response ? (headlineOf(a.response) ?? '') : a?.error ? `(${a.error.message})` : ''

/** The session as a Markdown transcript (question, headline, explanation, evidence counts). */
export function sessionMarkdown(s: StoredSession): string {
  const lines = [`# ${s.title}`, '']
  const where = [s.project_title, s.drawing_title ?? s.drawing_id, s.floor_label].filter(Boolean).join(' · ')
  lines.push(`- Drawing: ${where}`, `- Updated: ${new Date(s.updated_at).toLocaleString()}`, '')
  pairs(s).forEach(({ q, a }, i) => {
    lines.push(`## ${i + 1}. ${q.text}`, '')
    const head = answerText(a)
    if (head) lines.push(`**Answer:** ${head}`, '')
    if (a?.response?.explanation) lines.push(a.response.explanation, '')
    if (a?.response) {
      const ev = a.response.evidence
      const rejected = Object.values(a.toggles.rejected).filter(Boolean).length
      lines.push(
        `_Evidence: ${ev.boxes.length} boxes, ${ev.rooms.length} rooms${rejected ? `, ${rejected} rejected by you` : ''} · method ${a.response.method}_`,
        '',
      )
    }
  })
  return lines.join('\n')
}

/** One row per question: the answer value, unit, method, evidence and rejects. */
export function sessionCsv(s: StoredSession): string {
  const head = ['n', 'question', 'answer', 'value', 'unit', 'method', 'boxes', 'rooms', 'rejected', 'cost_usd']
  const rows = pairs(s).map(({ q, a }, i) => {
    const r = a?.response
    const ans = r?.answer
    const value = ans && 'value' in ans ? ans.value : undefined
    const unit = ans?.kind === 'area' ? 'm2' : ans?.kind === 'quantity' ? ans.unit : ans?.kind === 'count' ? ans.subject : ''
    return [
      i + 1,
      q.text,
      answerText(a),
      value,
      unit,
      r?.method,
      r?.evidence.boxes.length,
      r?.evidence.rooms.length,
      a ? Object.values(a.toggles.rejected).filter(Boolean).length : undefined,
      r?.cost_usd,
    ]
      .map(csvCell)
      .join(',')
  })
  return [head.join(','), ...rows].join('\n')
}

export const sessionFileName = (s: StoredSession, ext: 'md' | 'csv') =>
  `aura-session-${(s.title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || s.id).slice(0, 48)}.${ext}`

export const questionsLabel = (s: StoredSession) => plural(uiLang.get(), 'hist_questions', questionCount(s))

/** Open the history sheet from anywhere (keybinds, palette, slash commands). */
export function openSessionHistory() {
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(OPEN_HISTORY_EVENT))
}

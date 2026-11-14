import { createStore, type StoreApi } from 'zustand/vanilla'
import type { AskResponse, AskRoute, Box, ChatEvent, Check, Drawing, EvidenceRoom, Lang, Override, PctRect, RankedCheck, Scope, TraceNode } from '@/api/contract'
import { ApiError, TransportError, spentOf } from '@/api/client'
import { createSource, type Source, type SourceStatus } from '@/api/source'
import { BUNDLED_DRAWING_ID, bundledDrawing } from '@/data/drawing'
import { emptyToggles, isRoomEvidenceId, roomIdOfEvidence, type Toggles } from '@/lib/answer'
import { detect } from '@/lib/detect'
import { clampRect } from '@/lib/geometry'
import { t } from '@/i18n/strings'

export const ZOOM_STEPS = [100, 140, 180, 240] as const

export type Tool = 'select' | 'mark' | 'whatif'
export type Feedback = 'up' | 'down' | null

export interface UserTurn {
  id: string
  role: 'user'
  text: string
  lang: Lang
  scope?: Scope
  markId?: string
  overrides?: Override[]
}

export interface AgentTurn {
  id: string
  role: 'agent'
  /** The user turn this answers. */
  replyTo: string
  status: 'pending' | 'done' | 'error'
  /** Live state while a streamed (/chat) answer arrives; kept after it finishes for the step list. */
  stream?: StreamState
  response?: AskResponse
  error?: { message: string; code?: string; status?: number }
  /** This turn was answered locally after the API failed (show a toast once). */
  fellBack?: boolean
  toggles: Toggles
  listOpen: boolean
  showCanonical: boolean
  feedback: Feedback
}

/** What the /chat stream has told us so far about one answer. */
export interface StreamState {
  phase: 'sent' | 'understood' | 'working' | 'checking'
  understood?: { canonical_en: string; detected_lang?: Lang; route?: AskRoute }
  /** Tool-call trace nodes in arrival order. */
  nodes: TraceNode[]
  /** Ids of nodes whose result has arrived. */
  resolved: string[]
  /** Evidence collected from tool results (lit on the plan before the answer lands). */
  evidence: { boxes: Box[]; rooms: EvidenceRoom[] }
  check?: Check & Partial<Omit<RankedCheck, keyof Check>>
}

export type Turn = UserTurn | AgentTurn

const CONVERSATION_KEY = (drawingId: string) => `aura.conversation.${drawingId}`
const readConversation = (drawingId: string): string | null => {
  try {
    return globalThis.localStorage?.getItem(CONVERSATION_KEY(drawingId)) ?? null
  } catch {
    return null
  }
}
const writeConversation = (drawingId: string, id: string | null) => {
  try {
    if (id) globalThis.localStorage?.setItem(CONVERSATION_KEY(drawingId), id)
    else globalThis.localStorage?.removeItem(CONVERSATION_KEY(drawingId))
  } catch {
    /* storage unavailable: the conversation lives for this page only */
  }
}

/** Fold one chat event into the stream state (pure; exported for tests). */
export function reduceStream(st: StreamState, e: ChatEvent): StreamState {
  switch (e.event) {
    case 'understood':
      return { ...st, phase: 'understood', understood: e.data }
    case 'tool_call':
      return { ...st, phase: 'working', nodes: [...st.nodes.filter((n) => n.id !== e.data.node.id), e.data.node] }
    case 'tool_result': {
      const ids = new Set(st.evidence.boxes.map((b) => b.id))
      const rooms = new Set(st.evidence.rooms.map((r) => r.room_id))
      return {
        ...st,
        resolved: st.resolved.includes(e.data.node_id) ? st.resolved : [...st.resolved, e.data.node_id],
        evidence: {
          boxes: [...st.evidence.boxes, ...(e.data.evidence.boxes ?? []).filter((b) => !ids.has(b.id))],
          rooms: [...st.evidence.rooms, ...(e.data.evidence.rooms ?? []).filter((r) => !rooms.has(r.room_id))],
        },
      }
    }
    case 'check':
      return { ...st, phase: 'checking', check: e.data.check }
    default:
      return st
  }
}

export interface Mark {
  id: string
  label: string
  rect: PctRect
}

export interface UndoEntry {
  turn_id: string
  evidence_id: string
  /** Value of the toggle before the change (undefined = untouched). */
  prev: boolean | undefined
}

export interface AskOptions {
  scope?: Scope
  overrides?: Override[]
}

/** Loading state of the drawing named in the URL. `session.drawing` changes only once a load is `ready`. */
export interface DrawingLoad {
  id: string
  state: 'loading' | 'processing' | 'ready' | 'failed' | 'too_slow' | 'not_found' | 'unavailable' | 'error'
  /** 0–1 while processing. */
  progress?: number
  /** Reason shown to the user (failed / error). */
  message?: string
}

/** How often a drawing that is still processing is polled. */
export const POLL_MS = 1500
/** A drawing still processing after this long is reported as stuck and no longer polled. */
export const POLL_DEADLINE_MS = 3 * 60_000

export interface AppState {
  session: {
    source: SourceStatus
    drawing: Drawing
    lang: Lang
    load: DrawingLoad
    conversationId: string | null
    /** Model use outside answers (e.g. /rewrite), counted in the session total. */
    spend: { calls: number; cost_usd: number }
  }
  turns: Turn[]
  ui: {
    activeTurnId: string | null
    selectedEvidenceId: string | null
    hoveredEvidenceIds: string[]
    tool: Tool
    zoomIndex: number
    compare: { on: boolean; mode: 'swipe' | 'blend'; opacity: number }
    marks: Mark[]
    attachedMarkId: string | null
    traceOpen: boolean
    pluginsOpen: boolean
    whatIf: { roomId: string | null; w: number; h: number }
  }
  undoStack: UndoEntry[]

  // session
  initSource(): Promise<void>
  retrySource(): Promise<void>
  /** Show drawing `id`: the bundled one instantly, others from the API (polling while processing). */
  loadDrawing(id: string): Promise<void>
  /** Stop polling (route left). */
  stopLoading(): void
  // turns
  ask(text: string, opts?: AskOptions): Promise<string>
  /** Stop the answer that is streaming (the card says it was stopped; nothing is guessed). */
  cancel(): void
  /** Restore the drawing's last conversation from the API (after a reload); no-op when there are turns. */
  restoreConversation(): Promise<void>
  /** Forget the conversation: a fresh start on the same drawing. */
  newConversation(): void
  /** Record model use that is not part of an answer (a rewrite), so the session total stays honest. */
  addSpend(calls: number, cost_usd: number): void
  setActive(turnId: string | null): void
  setListOpen(turnId: string, open: boolean): void
  toggleCanonical(turnId: string): void
  setFeedback(turnId: string, feedback: Feedback): void
  // evidence
  select(turnId: string, evidenceId: string | null): void
  toggle(turnId: string, evidenceId: string): void
  /** First tap selects/locates; a second tap on the selected evidence toggles it. */
  tap(turnId: string, evidenceId: string): 'selected' | 'toggled'
  hover(evidenceIds: string[]): void
  undo(): UndoEntry | null
  resetTurn(turnId: string): void
  // tools and view
  setTool(tool: Tool): void
  setZoomIndex(i: number): void
  zoomIn(): void
  zoomOut(): void
  setCompare(patch: Partial<AppState['ui']['compare']>): void
  addMark(rect: PctRect, label?: string): string
  removeMark(id: string): void
  attachMark(id: string | null): void
  setTraceOpen(open: boolean): void
  setPluginsOpen(open: boolean): void
  // what-if
  startWhatIf(roomId: string): void
  setWhatIfDraft(w: number, h: number): void
  cancelWhatIf(): void
  /** Re-asks the area of the room's apartment with the draft size as an override. */
  applyWhatIf(): Promise<string | null>
}

export interface StoreDeps {
  source?: Source
  drawing?: Drawing
  pollMs?: number
  pollDeadlineMs?: number
}

export type AppStore = StoreApi<AppState>

let seq = 0
const nextId = (p: string) => `${p}-${++seq}`

const clampZoom = (i: number) => Math.min(ZOOM_STEPS.length - 1, Math.max(0, Math.round(i)))

export function createAppStore(deps: StoreDeps = {}): AppStore {
  const source = deps.source ?? createSource()
  const initial = deps.drawing ?? bundledDrawing
  const pollMs = deps.pollMs ?? POLL_MS
  const pollDeadlineMs = deps.pollDeadlineMs ?? POLL_DEADLINE_MS

  return createStore<AppState>()((set, get) => {
    const patchAgent = (turnId: string, patch: (t: AgentTurn) => Partial<AgentTurn>) =>
      set((s) => ({
        turns: s.turns.map((x) => (x.id === turnId && x.role === 'agent' ? { ...x, ...patch(x) } : x)),
      }))
    const agent = (turnId: string): AgentTurn | undefined => {
      const x = get().turns.find((y) => y.id === turnId)
      return x && x.role === 'agent' ? x : undefined
    }
    const ui = (patch: Partial<AppState['ui']>) => set((s) => ({ ui: { ...s.ui, ...patch } }))

    /** Write one toggle value; `undefined` restores the source's default. */
    const writeToggle = (turnId: string, evidenceId: string, value: boolean | undefined) =>
      patchAgent(turnId, (x) => {
        const key = isRoomEvidenceId(evidenceId) ? 'included' : 'rejected'
        const map = { ...x.toggles[key] }
        if (value === undefined) delete map[evidenceId]
        else map[evidenceId] = value
        return { toggles: { ...x.toggles, [key]: map } }
      })

    source.subscribe((st) => set((s) => ({ session: { ...s.session, source: st } })))

    const freshUi = (): AppState['ui'] => ({
        activeTurnId: null,
        selectedEvidenceId: null,
        hoveredEvidenceIds: [],
        tool: 'select',
        zoomIndex: 0,
        compare: { on: false, mode: 'swipe', opacity: 55 },
        marks: [],
        attachedMarkId: null,
        traceOpen: false,
        pluginsOpen: false,
        whatIf: { roomId: null, w: 0, h: 0 },
      })

    /** Each loadDrawing call takes a ticket; results of an older ticket are ignored. */
    let ticket = 0
    /** The answer currently streaming; a new question or Stop aborts it. */
    let inflight: AbortController | null = null
    let pollTimer: ReturnType<typeof setTimeout> | undefined
    const setLoad = (load: DrawingLoad) => set((s) => ({ session: { ...s.session, load } }))
    /** Swap in a ready drawing; a different drawing starts a clean conversation. */
    const showDrawing = (d: Drawing) => {
      const session = { ...get().session, drawing: d, load: { id: d.id, state: 'ready' } as DrawingLoad }
      if (get().session.drawing.id === d.id) return set({ session })
      // Another drawing: an answer still streaming for the old one must not land here (its conversation id
      // belongs to the old drawing; the next question would be rejected).
      inflight?.abort()
      inflight = null
      set({ session: { ...session, conversationId: null }, turns: [], undoStack: [], ui: freshUi() })
    }
    const loadError = (id: string, e: unknown): DrawingLoad => {
      if (e instanceof ApiError && e.code === 'unknown_drawing') return { id, state: 'not_found' }
      if (e instanceof ApiError && e.status === 404) return { id, state: 'not_found' }
      // A 5xx means the server is there but failed: say so, not "not connected".
      if (e instanceof TransportError && e.kind === 'server')
        return { id, state: 'error', message: 'The server had a problem opening this drawing. Try again in a moment.' }
      if (e instanceof TransportError) return { id, state: 'unavailable', message: e.message }
      return { id, state: 'error', message: e instanceof Error ? e.message : 'The drawing could not be loaded' }
    }

    return {
      session: {
        source: source.status(),
        drawing: initial,
        lang: 'en',
        load: { id: initial.id, state: 'ready' },
        conversationId: null,
        spend: { calls: 0, cost_usd: 0 },
      },
      turns: [],
      ui: freshUi(),
      undoStack: [],

      async initSource() {
        await source.init()
      },
      async retrySource() {
        await source.retry()
      },

      async loadDrawing(id) {
        const my = ++ticket
        clearTimeout(pollTimer)
        const live = () => my === ticket
        if (id === BUNDLED_DRAWING_ID || id === get().session.drawing.id) {
          showDrawing(id === BUNDLED_DRAWING_ID ? bundledDrawing : get().session.drawing)
          return
        }
        if (!source.hasApi()) {
          setLoad({ id, state: 'unavailable' })
          return
        }
        setLoad({ id, state: 'loading' })
        const fetchFull = async () => {
          const d = await source.drawing(id)
          if (live()) showDrawing(d)
        }
        const started = Date.now()
        const poll = async (): Promise<void> => {
          try {
            const st = await source.drawingStatus(id)
            if (!live()) return
            if (st.status === 'ready') return await fetchFull()
            if (st.status === 'failed') return setLoad({ id, state: 'failed', message: st.error })
            if (Date.now() - started > pollDeadlineMs) return setLoad({ id, state: 'too_slow' })
            setLoad({ id, state: 'processing', ...(st.progress !== undefined ? { progress: st.progress } : {}) })
            await new Promise<void>((r) => (pollTimer = setTimeout(r, pollMs)))
            if (live()) return await poll()
          } catch (e) {
            if (live()) setLoad(loadError(id, e))
          }
        }
        try {
          await fetchFull()
        } catch (e) {
          if (!live()) return
          if (e instanceof ApiError && e.code === 'processing') return poll()
          setLoad(loadError(id, e))
        }
      },
      stopLoading() {
        ticket++
        clearTimeout(pollTimer)
      },

      async ask(text, opts = {}) {
        const s = get()
        const mark = s.ui.marks.find((m) => m.id === s.ui.attachedMarkId)
        const scope: Scope | undefined =
          opts.scope ?? (mark ? { kind: 'region', rect: mark.rect, label: mark.label } : undefined)
        const user: UserTurn = {
          id: nextId('u'),
          role: 'user',
          text,
          lang: detect(text),
          ...(scope ? { scope } : {}),
          ...(mark && !opts.scope ? { markId: mark.id } : {}),
          ...(opts.overrides?.length ? { overrides: opts.overrides } : {}),
        }
        const pending: AgentTurn = {
          id: nextId('a'),
          role: 'agent',
          replyTo: user.id,
          status: 'pending',
          toggles: emptyToggles(),
          listOpen: true,
          showCanonical: false,
          feedback: null,
          // With the API the answer streams: show the step list (and Stop) from the first moment.
          ...(source.status().kind === 'api'
            ? { stream: { phase: 'sent' as const, nodes: [], resolved: [], evidence: { boxes: [], rooms: [] } } }
            : {}),
        }
        set((st) => ({
          turns: [...st.turns.map((x) => (x.role === 'agent' ? { ...x, listOpen: false } : x)), user, pending],
          ui: { ...st.ui, activeTurnId: pending.id, selectedEvidenceId: null, hoveredEvidenceIds: [], attachedMarkId: null },
        }))
        inflight?.abort()
        const ctrl = new AbortController()
        inflight = ctrl
        const drawingId = get().session.drawing.id
        try {
          const { response, fellBack } = await source.chat(
            {
              drawing_id: drawingId,
              question: text,
              ...(get().session.conversationId ? { conversation_id: get().session.conversationId! } : {}),
              ...(scope ? { scope } : {}),
              ...(opts.overrides?.length ? { overrides: opts.overrides } : {}),
              lang_hint: get().session.lang,
            },
            (e) => {
              if (ctrl.signal.aborted) return
              if (e.event === 'turn') {
                writeConversation(drawingId, e.data.conversation_id)
                if (get().session.drawing.id !== drawingId) return
                set((st) => ({ session: { ...st.session, conversationId: e.data.conversation_id } }))
                return
              }
              patchAgent(pending.id, (x) => ({
                stream: reduceStream(x.stream ?? { phase: 'sent', nodes: [], resolved: [], evidence: { boxes: [], rooms: [] } }, e),
              }))
            },
            ctrl.signal,
          )
          // Stopped after the answer arrived: keep the answer (it is complete); Stop only ends a pending one.
          if (ctrl.signal.aborted && get().session.drawing.id !== drawingId) throw new DOMException('Stopped', 'AbortError')
          patchAgent(pending.id, () => ({ status: 'done', response, ...(fellBack ? { fellBack: true } : {}) }))
          set((st) => ({ session: { ...st.session, lang: response.detected_lang } }))
        } catch (e) {
          if (inflight === ctrl) inflight = null
          const spent = spentOf(e)
          if (spent.calls || spent.cost_usd) get().addSpend(spent.calls, spent.cost_usd)
          if (ctrl.signal.aborted || (e instanceof DOMException && e.name === 'AbortError')) {
            patchAgent(pending.id, () => ({ status: 'error', error: { message: t(get().session.lang, 'stopped'), code: 'cancelled' } }))
            return pending.id
          }
          // Browser transport strings ("Failed to fetch") are not for people; say what happened instead.
          const error =
            e instanceof ApiError
              ? { message: e.message, code: e.code, status: e.status }
              : e instanceof TransportError
                ? {
                    message:
                      e.kind === 'timeout'
                        ? 'No answer within a minute. Try again, or ask a simpler question.'
                        : e.kind === 'server'
                          ? 'The server had a problem with this question. Try again in a moment.'
                          : 'The server could not be reached. Try again in a moment.',
                  }
                : { message: e instanceof Error ? e.message : 'Something went wrong' }
          patchAgent(pending.id, () => ({ status: 'error', error }))
        }
        if (inflight === ctrl) inflight = null
        return pending.id
      },

      cancel() {
        inflight?.abort()
        inflight = null
      },

      async restoreConversation() {
        const s = get()
        if (s.turns.length) return
        const drawingId = s.session.drawing.id
        const id = readConversation(drawingId)
        if (!id) return
        let conv
        try {
          conv = await source.conversation(id)
        } catch {
          return
        }
        if (get().turns.length || get().session.drawing.id !== drawingId) return
        if (!conv) {
          writeConversation(drawingId, null)
          return
        }
        const turns: Turn[] = []
        for (const ct of conv.turns) {
          const user: UserTurn = { id: nextId('u'), role: 'user', text: ct.message, lang: detect(ct.message) }
          const agent: AgentTurn = {
            id: nextId('a'),
            role: 'agent',
            replyTo: user.id,
            status: ct.response ? 'done' : 'error',
            ...(ct.response
              ? { response: ct.response }
              : { error: { message: ct.error?.error ?? t(get().session.lang, 'something_wrong'), code: ct.error?.code } }),
            toggles: emptyToggles(),
            listOpen: false,
            showCanonical: false,
            feedback: null,
          }
          turns.push(user, agent)
        }
        const last = [...turns].reverse().find((x): x is AgentTurn => x.role === 'agent' && x.status === 'done')
        if (last) last.listOpen = true
        set((st) => ({
          turns,
          session: { ...st.session, conversationId: conv.id },
          ui: { ...st.ui, activeTurnId: last?.id ?? null },
        }))
      },

      addSpend(calls, cost_usd) {
        set((st) => ({
          session: {
            ...st.session,
            spend: { calls: st.session.spend.calls + calls, cost_usd: st.session.spend.cost_usd + cost_usd },
          },
        }))
      },

      newConversation() {
        inflight?.abort()
        inflight = null
        writeConversation(get().session.drawing.id, null)
        set((st) => ({ turns: [], undoStack: [], session: { ...st.session, conversationId: null }, ui: freshUi() }))
      },

      setActive(turnId) {
        ui({ activeTurnId: turnId, selectedEvidenceId: null, hoveredEvidenceIds: [] })
      },
      setListOpen(turnId, open) {
        patchAgent(turnId, () => ({ listOpen: open }))
      },
      toggleCanonical(turnId) {
        patchAgent(turnId, (x) => ({ showCanonical: !x.showCanonical }))
      },
      setFeedback(turnId, feedback) {
        patchAgent(turnId, () => ({ feedback }))
      },

      select(turnId, evidenceId) {
        ui({ activeTurnId: turnId, selectedEvidenceId: evidenceId })
      },
      toggle(turnId, evidenceId) {
        const x = agent(turnId)
        if (!x || !x.response) return
        let prev: boolean | undefined
        let next: boolean | undefined
        if (isRoomEvidenceId(evidenceId)) {
          const room = x.response.evidence.rooms.find((r) => r.room_id === roomIdOfEvidence(evidenceId))
          if (!room) return
          prev = x.toggles.included[evidenceId]
          const flipped = !(prev === undefined ? room.included : prev)
          next = flipped === room.included ? undefined : flipped
        } else {
          if (!x.response.evidence.boxes.some((b) => b.id === evidenceId)) return
          prev = x.toggles.rejected[evidenceId]
          next = prev ? undefined : true
        }
        writeToggle(turnId, evidenceId, next)
        set((s) => ({ undoStack: [...s.undoStack, { turn_id: turnId, evidence_id: evidenceId, prev }] }))
      },
      tap(turnId, evidenceId) {
        const { ui: u } = get()
        if (u.activeTurnId === turnId && u.selectedEvidenceId === evidenceId) {
          get().toggle(turnId, evidenceId)
          return 'toggled'
        }
        get().select(turnId, evidenceId)
        return 'selected'
      },
      hover(evidenceIds) {
        ui({ hoveredEvidenceIds: evidenceIds })
      },
      undo() {
        const stack = get().undoStack
        const last = stack[stack.length - 1]
        if (!last) return null
        writeToggle(last.turn_id, last.evidence_id, last.prev)
        set({ undoStack: stack.slice(0, -1) })
        return last
      },
      resetTurn(turnId) {
        patchAgent(turnId, () => ({ toggles: emptyToggles() }))
        set((s) => ({ undoStack: s.undoStack.filter((e) => e.turn_id !== turnId) }))
      },

      setTool(tool) {
        ui({ tool })
      },
      setZoomIndex(i) {
        ui({ zoomIndex: clampZoom(i) })
      },
      zoomIn() {
        ui({ zoomIndex: clampZoom(get().ui.zoomIndex + 1) })
      },
      zoomOut() {
        ui({ zoomIndex: clampZoom(get().ui.zoomIndex - 1) })
      },
      setCompare(patch) {
        const c = { ...get().ui.compare, ...patch }
        ui({ compare: { ...c, opacity: Math.min(100, Math.max(0, c.opacity)) } })
      },
      addMark(rect, label) {
        const marks = get().ui.marks
        const id = nextId('mark')
        const n = marks.length + 1
        ui({ marks: [...marks, { id, label: label ?? `Area ${n}`, rect: clampRect(rect) }], attachedMarkId: id })
        return id
      },
      removeMark(id) {
        const u = get().ui
        ui({
          marks: u.marks.filter((m) => m.id !== id),
          attachedMarkId: u.attachedMarkId === id ? null : u.attachedMarkId,
        })
      },
      attachMark(id) {
        ui({ attachedMarkId: id })
      },
      setTraceOpen(open) {
        ui({ traceOpen: open })
      },
      setPluginsOpen(open) {
        ui({ pluginsOpen: open })
      },

      startWhatIf(roomId) {
        const room = get().session.drawing.rooms.find((r) => r.room_id === roomId)
        if (!room?.rect) return
        ui({ tool: 'whatif', whatIf: { roomId, w: room.rect.w_m, h: room.rect.h_m } })
      },
      setWhatIfDraft(w, h) {
        const cur = get().ui.whatIf
        if (!cur.roomId) return
        ui({ whatIf: { ...cur, w: Math.max(0, w), h: Math.max(0, h) } })
      },
      cancelWhatIf() {
        ui({ tool: 'select', whatIf: { roomId: null, w: 0, h: 0 } })
      },
      async applyWhatIf() {
        const { whatIf } = get().ui
        const room = get().session.drawing.rooms.find((r) => r.room_id === whatIf.roomId)
        if (!room || !(whatIf.w > 0 && whatIf.h > 0)) return null
        const lang = get().session.lang
        const text = room.unit
          ? t(lang, 'whatif_question_unit', { unit: room.unit })
          : t(lang, 'whatif_question_room', { room: room.room_id })
        ui({ tool: 'select', whatIf: { roomId: null, w: 0, h: 0 } })
        // Explicit scope: an attached mark must not turn the what-if into a region question.
        const scope: Scope = room.unit ? { kind: 'unit', unit: room.unit } : { kind: 'room', room_id: room.room_id }
        return get().ask(text, { scope, overrides: [{ room_id: room.room_id, w_m: whatIf.w, h_m: whatIf.h }] })
      },
    }
  })
}

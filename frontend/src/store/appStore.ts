import { createStore, type StoreApi } from 'zustand/vanilla'
import type { AskResponse, Drawing, Lang, Override, PctRect, Scope } from '@/api/contract'
import { ApiError, TransportError } from '@/api/client'
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
  response?: AskResponse
  error?: { message: string; code?: string; status?: number }
  /** This turn was answered locally after the API failed (show a toast once). */
  fellBack?: boolean
  toggles: Toggles
  listOpen: boolean
  showCanonical: boolean
  feedback: Feedback
}

export type Turn = UserTurn | AgentTurn

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
  state: 'loading' | 'processing' | 'ready' | 'failed' | 'not_found' | 'unavailable' | 'error'
  /** 0–1 while processing. */
  progress?: number
  /** Reason shown to the user (failed / error). */
  message?: string
}

/** How often a drawing that is still processing is polled. */
export const POLL_MS = 1500

export interface AppState {
  session: { source: SourceStatus; drawing: Drawing; lang: Lang; load: DrawingLoad }
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
}

export type AppStore = StoreApi<AppState>

let seq = 0
const nextId = (p: string) => `${p}-${++seq}`

const clampZoom = (i: number) => Math.min(ZOOM_STEPS.length - 1, Math.max(0, Math.round(i)))

export function createAppStore(deps: StoreDeps = {}): AppStore {
  const source = deps.source ?? createSource()
  const initial = deps.drawing ?? bundledDrawing
  const pollMs = deps.pollMs ?? POLL_MS

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
    let pollTimer: ReturnType<typeof setTimeout> | undefined
    const setLoad = (load: DrawingLoad) => set((s) => ({ session: { ...s.session, load } }))
    /** Swap in a ready drawing; a different drawing starts a clean conversation. */
    const showDrawing = (d: Drawing) =>
      set((s) => {
        const session = { ...s.session, drawing: d, load: { id: d.id, state: 'ready' } as DrawingLoad }
        if (s.session.drawing.id === d.id) return { session }
        return { session, turns: [], undoStack: [], ui: freshUi() }
      })
    const loadError = (id: string, e: unknown): DrawingLoad => {
      if (e instanceof ApiError && e.code === 'unknown_drawing') return { id, state: 'not_found' }
      if (e instanceof ApiError && e.status === 404) return { id, state: 'not_found' }
      if (e instanceof TransportError) return { id, state: 'unavailable', message: e.message }
      return { id, state: 'error', message: e instanceof Error ? e.message : 'The drawing could not be loaded' }
    }

    return {
      session: { source: source.status(), drawing: initial, lang: 'en', load: { id: initial.id, state: 'ready' } },
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
        const poll = async (): Promise<void> => {
          try {
            const st = await source.drawingStatus(id)
            if (!live()) return
            if (st.status === 'ready') return await fetchFull()
            if (st.status === 'failed') return setLoad({ id, state: 'failed', message: st.error })
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
        }
        set((st) => ({
          turns: [...st.turns.map((x) => (x.role === 'agent' ? { ...x, listOpen: false } : x)), user, pending],
          ui: { ...st.ui, activeTurnId: pending.id, selectedEvidenceId: null, hoveredEvidenceIds: [], attachedMarkId: null },
        }))
        try {
          const { response, fellBack } = await source.ask({
            drawing_id: get().session.drawing.id,
            question: text,
            ...(scope ? { scope } : {}),
            ...(opts.overrides?.length ? { overrides: opts.overrides } : {}),
            lang_hint: get().session.lang,
          })
          patchAgent(pending.id, () => ({ status: 'done', response, ...(fellBack ? { fellBack: true } : {}) }))
          set((st) => ({ session: { ...st.session, lang: response.detected_lang } }))
        } catch (e) {
          const error =
            e instanceof ApiError
              ? { message: e.message, code: e.code, status: e.status }
              : { message: e instanceof Error ? e.message : 'Something went wrong' }
          patchAgent(pending.id, () => ({ status: 'error', error }))
        }
        return pending.id
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

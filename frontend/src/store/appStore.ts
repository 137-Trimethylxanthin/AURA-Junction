import { tu } from '@/i18n/uiLang'
import { createStore, type StoreApi } from 'zustand/vanilla'
import type {
  AnswererMode,
  AskResponse,
  AskRoute,
  Box,
  ChatEvent,
  Check,
  Drawing,
  EvidenceGroup,
  EvidenceLink,
  EvidenceRoom,
  Lang,
  Override,
  PctRect,
  ProjectBundle,
  ProjectFloor,
  RankedCheck,
  Scope,
  TraceNode,
} from '@/api/contract'
import { ApiError, TransportError, spentOf } from '@/api/client'
import { createSource, type Source, type SourceStatus } from '@/api/source'
import { BUNDLED_DRAWING_ID, bundledDrawing } from '@/data/drawing'
import { emptyToggles, isRoomEvidenceId, roomEvidenceId, roomIdOfEvidence, type Toggles } from '@/lib/answer'
import { detect } from '@/lib/detect'
import { clampRect } from '@/lib/geometry'
import { t } from '@/i18n/strings'

export const ZOOM_MIN = 100
export const ZOOM_MAX = 450
export const ZOOM_STEPS = [100, 140, 190, 260, 350, 450] as const

export type Tool = 'select' | 'mark' | 'whatif'
/** The plan as the flat drawing or as a 3D model built from it. */
export type PlanView = '2d' | '3d'
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
  /** 1.2: 1-based queue position while waiting for a deep-agent slot; cleared once execution starts. */
  queuePosition?: number
  understood?: { canonical_en: string; detected_lang?: Lang; route?: AskRoute }
  /** Tool-call trace nodes in arrival order. */
  nodes: TraceNode[]
  /** Ids of nodes whose result has arrived. */
  resolved: string[]
  /** Evidence collected from tool results (lit on the plan before the answer lands). */
  evidence: { boxes: Box[]; rooms: EvidenceRoom[]; groups?: EvidenceGroup[]; links?: EvidenceLink[] }
  check?: Check & Partial<Omit<RankedCheck, keyof Check>>
}

export type Turn = UserTurn | AgentTurn

export const CONVERSATION_KEY = (drawingId: string, projectId?: string | null) =>
  projectId ? `aura.conversation.project.${projectId}` : `aura.conversation.${drawingId}`

const readConversation = (drawingId: string, projectId?: string | null): string | null => {
  try {
    if (projectId) {
      const projConv = globalThis.localStorage?.getItem(CONVERSATION_KEY(drawingId, projectId))
      if (projConv) return projConv
    }
    return globalThis.localStorage?.getItem(CONVERSATION_KEY(drawingId)) ?? null
  } catch {
    return null
  }
}
const writeConversation = (drawingId: string, id: string | null, projectId?: string | null) => {
  try {
    const key = CONVERSATION_KEY(drawingId, projectId)
    if (id) globalThis.localStorage?.setItem(key, id)
    else globalThis.localStorage?.removeItem(key)
  } catch {
    /* storage unavailable: the conversation lives for this page only */
  }
}

function sortBundleFloors(floors: readonly ProjectFloor[]): ProjectFloor[] {
  return [...floors].sort((a, b) => a.order - b.order || a.drawing_id.localeCompare(b.drawing_id))
}

function projectIdForDrawing(project: ProjectBundle | null, drawingId: string): string | null {
  return project && project.floors.some((f) => f.drawing_id === drawingId) ? project.id : null
}

/**
 * Evidence items without a `drawing_id` get `floor` (the floor the question was asked on); items that name a
 * floor keep it. Pure; exported for tests.
 */
export function stampFloor<E extends { boxes: Box[]; rooms: EvidenceRoom[]; links?: EvidenceLink[] }>(ev: E, floor: string): E {
  const stamp = <T extends { drawing_id?: string }>(xs: T[]): T[] => xs.map((x) => (x.drawing_id ? x : { ...x, drawing_id: floor }))
  return {
    ...ev,
    boxes: stamp(ev.boxes as (Box & { drawing_id?: string })[]),
    rooms: stamp(ev.rooms as (EvidenceRoom & { drawing_id?: string })[]),
    ...(ev.links ? { links: stamp(ev.links as (EvidenceLink & { drawing_id?: string })[]) } : {}),
  }
}

/** Fold one chat event into the stream state (pure; exported for tests). */
export function reduceStream(st: StreamState, e: ChatEvent): StreamState {
  switch (e.event) {
    case 'progress':
      return { ...st, queuePosition: e.data.queue_position }
    case 'understood':
      return { ...st, queuePosition: undefined, phase: 'understood', understood: e.data }
    case 'tool_call':
      return {
        ...st,
        queuePosition: undefined,
        phase: 'working',
        nodes: [...st.nodes.filter((n) => n.id !== e.data.node.id), e.data.node],
      }
    case 'tool_result': {
      const ids = new Set(st.evidence.boxes.map((b) => b.id))
      const rooms = new Set(st.evidence.rooms.map((r) => r.room_id))
      const prevGroups = st.evidence.groups ?? []
      const incomingGroups = e.data.evidence.groups ?? []
      let mergedGroups: EvidenceGroup[] | undefined
      if (prevGroups.length > 0 || incomingGroups.length > 0) {
        const byId = new Map<string, EvidenceGroup>(prevGroups.map((g) => [g.id, g]))
        for (const g of incomingGroups) byId.set(g.id, g)
        mergedGroups = [...byId.values()]
      }
      const prevLinks = st.evidence.links ?? []
      const incomingLinks = e.data.evidence.links ?? []
      let mergedLinks: EvidenceLink[] | undefined
      if (prevLinks.length > 0 || incomingLinks.length > 0) {
        const byId = new Map<string, EvidenceLink>(prevLinks.map((l) => [l.id, l]))
        for (const l of incomingLinks) byId.set(l.id, l)
        mergedLinks = [...byId.values()]
      }
      return {
        ...st,
        queuePosition: undefined,
        resolved: st.resolved.includes(e.data.node_id) ? st.resolved : [...st.resolved, e.data.node_id],
        evidence: {
          boxes: [...st.evidence.boxes, ...(e.data.evidence.boxes ?? []).filter((b) => !ids.has(b.id))],
          rooms: [...st.evidence.rooms, ...(e.data.evidence.rooms ?? []).filter((r) => !rooms.has(r.room_id))],
          ...(mergedGroups ? { groups: mergedGroups } : {}),
          ...(mergedLinks ? { links: mergedLinks } : {}),
        },
      }
    }
    case 'check':
      return { ...st, queuePosition: undefined, phase: 'checking', check: e.data.check }
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
    /** 1.2: active backend answerer mode reported by /health ('hybrid' by default). */
    answererMode: AnswererMode
    drawing: Drawing
    lang: Lang
    load: DrawingLoad
    conversationId: string | null
    /** Model use outside answers (e.g. /rewrite), counted in the session total. */
    spend: { calls: number; cost_usd: number }
  }
  /** Top-level alias mirrored with `session.answererMode` for ergonomic selector access. */
  answererMode: AnswererMode
  /** 1.2: Active multi-plan house/project bundle when the open drawing belongs to one. */
  activeProject: ProjectBundle | null
  /** 1.2: When true on a `>=2`-floor `activeProject`, `ask()` sends `ScopeProject` across all floors. */
  crossPlanScope: boolean
  /** 1.2: Spotlighted categorical `EvidenceGroup.id` (mirrored on `ui.focusedGroupId`). */
  focusedGroupId: string | null
  /** 1.2: Loaded/warmed `Drawing` cache keyed by `drawing.id` for `<50ms` floor switches & cross-floor crops. */
  drawingById: Record<string, Drawing>
  turns: Turn[]
  ui: {
    activeTurnId: string | null
    selectedEvidenceId: string | null
    hoveredEvidenceIds: string[]
    focusedGroupId: string | null
    tool: Tool
    /** Continuous zoom percentage in `[ZOOM_MIN, ZOOM_MAX]` (`100..450`). */
    zoom: number
    /** Nearest step index in `ZOOM_STEPS`, kept synchronized with `zoom`. */
    zoomIndex: number
    compare: { on: boolean; mode: 'swipe' | 'blend'; opacity: number }
    marks: Mark[]
    attachedMarkId: string | null
    traceOpen: boolean
    pluginsOpen: boolean
    shortcutsOpen: boolean
    commandOpen: boolean
    whatIf: { roomId: string | null; w: number; h: number }
    /** Kept across conversations and drawings: a presenter who switched to 3D stays there. */
    view: PlanView
  }
  undoStack: UndoEntry[]

  // session & multi-plan project bundles
  initSource(): Promise<void>
  retrySource(): Promise<void>
  setAnswererMode(mode: AnswererMode): void
  setActiveProject(bundle: ProjectBundle | null): void
  setCrossPlanScope(on: boolean): void
  setFocusedGroup(groupId: string | null): void
  /** Step `+1` (up) or `-1` (down) through `activeProject.floors` ordered by `order`; returns target `drawing_id` or `null`. */
  stepFloor(delta: number): string | null
  /** Pre-fetch and cache a sibling floor `Drawing` without swapping `session.drawing`. */
  warmDrawing(id: string): Promise<Drawing | null>
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
  /** Forget the conversation: a fresh start on the same drawing. Returns a 1-click restore callback. */
  newConversation(): () => void
  /** Restore the last cleared conversation on the current drawing, if no new turns were added. */
  undoNewConversation(): boolean
  /** Record model use that is not part of an answer (a rewrite), so the session total stays honest. */
  addSpend(calls: number, cost_usd: number): void
  setActive(turnId: string | null): void
  setListOpen(turnId: string, open: boolean): void
  toggleCanonical(turnId: string): void
  setFeedback(turnId: string, feedback: Feedback): void
  // evidence
  select(turnId: string, evidenceId: string | null): void
  /** Cycle through evidence items (`+1` next, `-1` previous) on the active or latest answered turn. */
  stepEvidence(delta: number): string | null
  toggle(turnId: string, evidenceId: string): void
  /** First tap selects/locates; a second tap on the selected evidence toggles it. */
  tap(turnId: string, evidenceId: string): 'selected' | 'toggled'
  hover(evidenceIds: string[]): void
  undo(): UndoEntry | null
  resetTurn(turnId: string): void
  // tools and view
  setTool(tool: Tool): void
  setZoom(zoom: number): void
  resetZoom(): void
  setView(view: PlanView): void
  setZoomIndex(i: number): void
  zoomIn(): void
  zoomOut(): void
  setCompare(patch: Partial<AppState['ui']['compare']>): void
  addMark(rect: PctRect, label?: string): string
  removeMark(id: string): void
  attachMark(id: string | null): void
  setTraceOpen(open: boolean): void
  setPluginsOpen(open: boolean): void
  setShortcutsOpen(open: boolean): void
  setCommandOpen(open: boolean): void
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
const clampZoomPct = (z: number) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(z)))
const nearestZoomIndex = (z: number) =>
  ZOOM_STEPS.reduce((best, step, i) => (Math.abs(step - z) < Math.abs(ZOOM_STEPS[best] - z) ? i : best), 0)

function canToggleEvidence(x: AgentTurn | undefined, evidenceId: string): boolean {
  if (!x?.response) return false
  if (x.response.method === 'agent_estimate' && (x.response.answer.kind !== 'count' || isRoomEvidenceId(evidenceId))) {
    return false
  }
  if (isRoomEvidenceId(evidenceId)) {
    return x.response.evidence.rooms.some((r) => r.room_id === roomIdOfEvidence(evidenceId))
  }
  return x.response.evidence.boxes.some((b) => b.id === evidenceId)
}

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

    source.subscribe((st) =>
      set((s) => {
        const mode = (st as { answerer?: AnswererMode }).answerer ?? s.session.answererMode
        return { answererMode: mode, session: { ...s.session, source: st, answererMode: mode } }
      }),
    )

    const freshUi = (view: PlanView = get()?.ui?.view ?? '2d'): AppState['ui'] => ({
        view,
        activeTurnId: null,
        selectedEvidenceId: null,
        hoveredEvidenceIds: [],
        focusedGroupId: null,
        tool: 'select',
        zoom: ZOOM_MIN,
        zoomIndex: 0,
        compare: { on: false, mode: 'swipe', opacity: 55 },
        marks: [],
        attachedMarkId: null,
        traceOpen: false,
        pluginsOpen: false,
        shortcutsOpen: false,
        commandOpen: false,
        whatIf: { roomId: null, w: 0, h: 0 },
      })

    /** Each loadDrawing call takes a ticket; results of an older ticket are ignored. */
    let ticket = 0
    /** The answer currently streaming; a new question or Stop aborts it. */
    let inflight: AbortController | null = null
    let pollTimer: ReturnType<typeof setTimeout> | undefined
    let lastClearedRestore: (() => boolean) | null = null
    const setLoad = (load: DrawingLoad) => set((s) => ({ session: { ...s.session, load } }))
    /** Swap in a ready drawing; sibling floors in `activeProject` keep the shared conversation & evidence. */
    const showDrawing = (d: Drawing) => {
      const cur = get()
      const nextMap = { ...cur.drawingById, [d.id]: d }
      const session = { ...cur.session, drawing: d, load: { id: d.id, state: 'ready' } as DrawingLoad }
      if (cur.session.drawing.id === d.id) return set({ session, drawingById: nextMap })

      const oldProjId = projectIdForDrawing(cur.activeProject, cur.session.drawing.id)
      const newProjId = projectIdForDrawing(cur.activeProject, d.id)
      const isSameProjectSwitch = Boolean(oldProjId && newProjId && oldProjId === newProjId)

      if (isSameProjectSwitch) {
        set({
          session,
          drawingById: nextMap,
          ui: {
            ...cur.ui,
            compare: d.images.original ? cur.ui.compare : { ...cur.ui.compare, on: false },
            hoveredEvidenceIds: [],
            marks: [],
            attachedMarkId: null,
            tool: cur.ui.tool === 'whatif' ? 'select' : cur.ui.tool,
            whatIf: { roomId: null, w: 0, h: 0 },
          },
        })
        return
      }

      // Unrelated drawing: an answer still streaming for the old one must not land here (its conversation id
      // belongs to the old drawing; the next question would be rejected).
      inflight?.abort()
      inflight = null
      lastClearedRestore = null
      set({
        session: { ...session, conversationId: null },
        drawingById: nextMap,
        activeProject: newProjId ? cur.activeProject : null,
        crossPlanScope: newProjId ? cur.crossPlanScope : false,
        focusedGroupId: null,
        turns: [],
        undoStack: [],
        ui: freshUi(),
      })
    }
    const loadError = (id: string, e: unknown): DrawingLoad => {
      if (e instanceof ApiError && e.code === 'unknown_drawing') return { id, state: 'not_found' }
      if (e instanceof ApiError && e.status === 404) return { id, state: 'not_found' }
      // A 5xx means the server is there but failed: say so, not "not connected".
      if (e instanceof TransportError && e.kind === 'server')
        return { id, state: 'error', message: tu('open_server_error') }
      if (e instanceof TransportError) return { id, state: 'unavailable', message: e.message }
      return { id, state: 'error', message: e instanceof Error ? e.message : tu('load_error') }
    }

    const initialSource = source.status()
    const initialMode: AnswererMode = (initialSource as { answerer?: AnswererMode }).answerer ?? 'hybrid'

    return {
      session: {
        source: initialSource,
        answererMode: initialMode,
        drawing: initial,
        lang: 'en',
        load: { id: initial.id, state: 'ready' },
        conversationId: null,
        spend: { calls: 0, cost_usd: 0 },
      },
      answererMode: initialMode,
      activeProject: null,
      crossPlanScope: false,
      focusedGroupId: null,
      drawingById: { [BUNDLED_DRAWING_ID]: bundledDrawing, [initial.id]: initial },
      turns: [],
      ui: freshUi(),
      undoStack: [],

      async initSource() {
        await source.init()
      },
      async retrySource() {
        await source.retry()
      },
      setAnswererMode(mode) {
        set((s) => ({ answererMode: mode, session: { ...s.session, answererMode: mode } }))
      },
      setActiveProject(bundle) {
        const normalized = bundle ? { ...bundle, floors: sortBundleFloors(bundle.floors) } : null
        set((s) => ({
          activeProject: normalized,
          crossPlanScope: normalized && normalized.floors.length >= 2 ? s.crossPlanScope : false,
        }))
        const after = get()
        const projId = projectIdForDrawing(normalized, after.session.drawing.id)
        if (!projId) return
        if (after.session.conversationId) {
          writeConversation(after.session.drawing.id, after.session.conversationId, projId)
        } else if (
          after.turns.length === 0 &&
          after.session.load.state === 'ready' &&
          source.status().kind === 'api' &&
          readConversation(after.session.drawing.id, projId)
        ) {
          void after.restoreConversation()
        }
      },
      setCrossPlanScope(on) {
        set({ crossPlanScope: Boolean(on) })
      },
      setFocusedGroup(groupId) {
        set((s) => ({ focusedGroupId: groupId, ui: { ...s.ui, focusedGroupId: groupId } }))
      },
      stepFloor(delta) {
        if (!delta) return null
        const s = get()
        if (!s.activeProject || s.activeProject.floors.length < 2) return null
        const floors = sortBundleFloors(s.activeProject.floors)
        const curIdx = floors.findIndex((f) => f.drawing_id === s.session.drawing.id)
        if (curIdx === -1) return null
        const nextIdx = curIdx + (delta > 0 ? 1 : -1)
        if (nextIdx < 0 || nextIdx >= floors.length) return null
        return floors[nextIdx].drawing_id
      },
      async warmDrawing(id) {
        const cached = get().drawingById[id]
        if (cached) return cached
        if (id === BUNDLED_DRAWING_ID) {
          set((s) => ({ drawingById: { ...s.drawingById, [BUNDLED_DRAWING_ID]: bundledDrawing } }))
          return bundledDrawing
        }
        if (!source.hasApi()) return null
        try {
          const d = await source.drawing(id)
          set((s) => ({ drawingById: { ...s.drawingById, [d.id]: d } }))
          return d
        } catch {
          return null
        }
      },

      async loadDrawing(id) {
        const my = ++ticket
        clearTimeout(pollTimer)
        const live = () => my === ticket
        if (id === BUNDLED_DRAWING_ID || id === get().session.drawing.id) {
          showDrawing(id === BUNDLED_DRAWING_ID ? bundledDrawing : get().session.drawing)
          return
        }
        const cur = get()
        const cachedSibling =
          projectIdForDrawing(cur.activeProject, id) && cur.drawingById[id] ? cur.drawingById[id] : undefined
        if (cachedSibling) {
          showDrawing(cachedSibling)
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
        const activeProjId = projectIdForDrawing(s.activeProject, s.session.drawing.id)
        const projectScope: Scope | undefined =
          s.crossPlanScope && activeProjId && s.activeProject && s.activeProject.floors.length >= 2
            ? {
                kind: 'project',
                project_id: s.activeProject.id,
                drawing_ids: sortBundleFloors(s.activeProject.floors).map((f) => f.drawing_id),
              }
            : undefined
        const scope: Scope | undefined =
          opts.scope ?? (mark ? { kind: 'region', rect: mark.rect, label: mark.label } : projectScope)
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
          focusedGroupId: null,
          turns: [...st.turns.map((x) => (x.role === 'agent' ? { ...x, listOpen: false } : x)), user, pending],
          ui: {
            ...st.ui,
            activeTurnId: pending.id,
            selectedEvidenceId: null,
            hoveredEvidenceIds: [],
            focusedGroupId: null,
            attachedMarkId: null,
          },
        }))
        inflight?.abort()
        const ctrl = new AbortController()
        inflight = ctrl
        const drawingId = get().session.drawing.id
        // In a multi-floor building, evidence that does not name its floor belongs to the floor it was asked on,
        // not to whichever floor is on screen later (it is stamped once, as it arrives).
        const floorOf = projectIdForDrawing(get().activeProject, drawingId) ? drawingId : null
        let streamAnswered = false
        const currentProjId = () => projectIdForDrawing(get().activeProject, drawingId) ?? activeProjId
        const stillInContext = () => {
          const cur = get()
          if (cur.session.drawing.id === drawingId) return true
          const pid = currentProjId()
          return Boolean(pid && projectIdForDrawing(cur.activeProject, cur.session.drawing.id) === pid)
        }
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
              if (e.event === 'answer') streamAnswered = true
              if (ctrl.signal.aborted) return
              if (e.event === 'turn') {
                const projNow = currentProjId()
                writeConversation(drawingId, e.data.conversation_id, projNow)
                if (!stillInContext()) return
                set((st) => ({ session: { ...st.session, conversationId: e.data.conversation_id } }))
                return
              }
              patchAgent(pending.id, (x) => {
                const st = reduceStream(x.stream ?? { phase: 'sent', nodes: [], resolved: [], evidence: { boxes: [], rooms: [] } }, e)
                return { stream: floorOf ? { ...st, evidence: stampFloor(st.evidence, floorOf) } : st }
              })
            },
            ctrl.signal,
          )
          // Stopped after the SSE `answer` event arrived: keep the answer (it is complete); Stop ends pending/non-SSE turns.
          if (ctrl.signal.aborted && (!stillInContext() || !streamAnswered)) {
            throw new DOMException('Stopped', 'AbortError')
          }
          const answered = floorOf ? { ...response, evidence: stampFloor(response.evidence, floorOf) } : response
          patchAgent(pending.id, () => ({ status: 'done', response: answered, ...(fellBack ? { fellBack: true } : {}) }))
          if (stillInContext() && agent(pending.id)) {
            set((st) => ({ session: { ...st.session, lang: response.detected_lang } }))
          }
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
                : { message: e instanceof Error ? e.message : tu('err_title') }
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
        const projId = projectIdForDrawing(s.activeProject, drawingId)
        const id = readConversation(drawingId, projId)
        if (!id) return
        let conv
        try {
          conv = await source.conversation(id)
        } catch {
          return
        }
        const after = get()
        const sameCtx =
          after.session.drawing.id === drawingId ||
          Boolean(projId && projectIdForDrawing(after.activeProject, after.session.drawing.id) === projId)
        if (after.turns.length || !sameCtx) return
        if (!conv) {
          writeConversation(drawingId, null, projId)
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
              ? {
                  response: projectIdForDrawing(get().activeProject, drawingId)
                    ? { ...ct.response, evidence: stampFloor(ct.response.evidence, drawingId) }
                    : ct.response,
                }
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
        const cur = get()
        const projId = projectIdForDrawing(cur.activeProject, cur.session.drawing.id)
        const snap =
          cur.turns.length > 0 || cur.ui.marks.length > 0
            ? {
                drawingId: cur.session.drawing.id,
                projId,
                turns: cur.turns.filter((x) => !(x.role === 'agent' && x.status === 'pending')),
                undoStack: cur.undoStack,
                conversationId: cur.session.conversationId,
                focusedGroupId: cur.focusedGroupId,
                ui: cur.ui,
              }
            : null
        inflight?.abort()
        inflight = null
        writeConversation(cur.session.drawing.id, null, projId)
        if (projId && cur.activeProject) {
          for (const f of cur.activeProject.floors) {
            writeConversation(f.drawing_id, null, null)
          }
        }
        set((st) => ({
          focusedGroupId: null,
          turns: [],
          undoStack: [],
          session: { ...st.session, conversationId: null },
          ui: freshUi(),
        }))
        const restore = (): boolean => {
          if (!snap) return false
          const now = get()
          const sameCtx =
            now.session.drawing.id === snap.drawingId ||
            Boolean(snap.projId && projectIdForDrawing(now.activeProject, now.session.drawing.id) === snap.projId)
          if (!sameCtx || now.turns.length > 0) return false
          lastClearedRestore = null
          writeConversation(snap.drawingId, snap.conversationId, snap.projId)
          set((st) => ({
            focusedGroupId: snap.focusedGroupId,
            turns: snap.turns,
            undoStack: snap.undoStack,
            session: { ...st.session, conversationId: snap.conversationId },
            ui: { ...snap.ui, shortcutsOpen: false, commandOpen: false },
          }))
          return true
        }
        lastClearedRestore = snap ? restore : null
        return () => {
          restore()
        }
      },
      undoNewConversation() {
        return lastClearedRestore ? lastClearedRestore() : false
      },

      setActive(turnId) {
        set((s) => ({
          focusedGroupId: null,
          ui: { ...s.ui, activeTurnId: turnId, selectedEvidenceId: null, hoveredEvidenceIds: [], focusedGroupId: null },
        }))
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
      stepEvidence(delta) {
        const s = get()
        const candidates: AgentTurn[] = []
        if (s.ui.activeTurnId) {
          const act = agent(s.ui.activeTurnId)
          if (act) candidates.push(act)
        }
        for (let i = s.turns.length - 1; i >= 0; i--) {
          const t = s.turns[i]
          if (t.role === 'agent' && !candidates.some((c) => c.id === t.id)) candidates.push(t)
        }
        const target = candidates.find((t) => {
          const ev = t.response?.evidence ?? t.stream?.evidence
          return !!ev && (ev.boxes.length > 0 || ev.rooms.length > 0)
        })
        if (!target) return null
        const ev = (target.response?.evidence ?? target.stream?.evidence)!
        const curDrawingId = s.session.drawing.id
        const floorIds = [
          ...ev.boxes.filter((b) => !b.drawing_id || b.drawing_id === curDrawingId).map((b) => b.id),
          ...ev.rooms.filter((r) => !r.drawing_id || r.drawing_id === curDrawingId).map((r) => roomEvidenceId(r.room_id)),
        ]
        const allIds = [...ev.boxes.map((b) => b.id), ...ev.rooms.map((r) => roomEvidenceId(r.room_id))]
        const ids = floorIds.length > 0 ? floorIds : allIds
        if (ids.length === 0) return null
        const cur = s.ui.activeTurnId === target.id ? s.ui.selectedEvidenceId : null
        const curIdx = cur ? ids.indexOf(cur) : -1
        const nextIdx =
          curIdx === -1
            ? delta < 0
              ? ids.length - 1
              : 0
            : (((curIdx + delta) % ids.length) + ids.length) % ids.length
        const chosen = ids[nextIdx]
        get().select(target.id, chosen)
        return chosen
      },
      toggle(turnId, evidenceId) {
        const x = agent(turnId)
        if (!x || !x.response || !canToggleEvidence(x, evidenceId)) return
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
          if (!canToggleEvidence(agent(turnId), evidenceId)) return 'selected'
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
      setZoom(zoom) {
        const z = clampZoomPct(zoom)
        ui({ zoom: z, zoomIndex: nearestZoomIndex(z) })
      },
      resetZoom() {
        ui({ zoom: ZOOM_MIN, zoomIndex: 0 })
      },
      setView(view) {
        // What-if and the mark tool are 2D-only: leave them. An attached mark stays (the composer shows its chip).
        if (view === '3d' && get().ui.tool === 'whatif') get().cancelWhatIf()
        ui({ view, tool: view === '3d' ? 'select' : get().ui.tool })
      },
      setZoomIndex(i) {
        const idx = clampZoom(i)
        ui({ zoomIndex: idx, zoom: ZOOM_STEPS[idx] })
      },
      zoomIn() {
        const cur = get().ui.zoom
        const nextIdx = ZOOM_STEPS.findIndex((step) => step > cur)
        const idx = nextIdx === -1 ? ZOOM_STEPS.length - 1 : nextIdx
        ui({ zoomIndex: idx, zoom: ZOOM_STEPS[idx] })
      },
      zoomOut() {
        const cur = get().ui.zoom
        let idx = 0
        for (let i = ZOOM_STEPS.length - 1; i >= 0; i--) {
          if (ZOOM_STEPS[i] < cur) {
            idx = i
            break
          }
        }
        ui({ zoomIndex: idx, zoom: ZOOM_STEPS[idx] })
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
      setShortcutsOpen(open) {
        ui(open ? { shortcutsOpen: true, commandOpen: false } : { shortcutsOpen: false })
      },
      setCommandOpen(open) {
        ui(open ? { commandOpen: true, shortcutsOpen: false } : { commandOpen: false })
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

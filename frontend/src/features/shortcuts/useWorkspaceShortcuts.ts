import { useEffect } from 'react'
import { useNavigate } from 'react-router'
import {
  type Drawing,
  type Lang,
  type ProjectBundle,
  type ProjectFloor,
} from '@/api/contract'
import { listDrawings, listProjects } from '@/api/client'
import { apiBase, inferFloorOrderAndLabel, myProjects } from '@/api/library'
import { useDraft } from '@/features/chat/draft'
import { isEditable, overlayOpen, shouldIgnoreShortcut } from '@/features/plan/shared'
import { appStore, type AgentTurn, type AppState, type AppStore, type Tool } from '@/store'
import { runPowerAction, type PowerActionId } from './powerBindings'

export type { ProjectBundle, ProjectFloor }
export { inferFloorOrderAndLabel }

export const CLOSE_DIALOG_LABEL: Record<Lang, string> = {
  en: 'Close',
  fr: 'Fermer',
  fi: 'Sulje',
  de: 'Schliessen',
}

export function isMacPlatform(
  nav: { platform?: string; userAgent?: string } | undefined = typeof navigator !== 'undefined' ? navigator : undefined,
): boolean {
  const p = `${nav?.platform ?? ''} ${nav?.userAgent ?? ''}`
  return /Mac|iPhone|iPad|iPod/i.test(p)
}

/**
 * EN / FR / FI / DE `AltGr`-safe modifier extraction.
 * On Windows/Linux, `AltGr` often arrives either via `getModifierState('AltGraph')` or `ctrlKey && altKey`.
 * Treating `ctrlKey && altKey` as `isAltGr` keeps `hasCmdCtrl` false when typing `[` / `]` on FI/DE/FR layouts.
 */
export function readShortcutModifiers(
  e: Pick<KeyboardEvent, 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey'> & {
    getModifierState?: (keyArg: string) => boolean
  },
) {
  const isAltGr = Boolean(e.getModifierState?.('AltGraph')) || (e.ctrlKey && e.altKey)
  const hasCmdCtrl = e.metaKey || (e.ctrlKey && !isAltGr)
  const hasPlainAlt = e.altKey && !isAltGr
  return { isAltGr, hasCmdCtrl, hasPlainAlt }
}

/** Time `H` went down: holding it longer than {@link PEEK_HOLD_MS} makes peek momentary (released on keyup). */
let peekDownAt = 0
const PEEK_HOLD_MS = 350

const hydrationSeq = new WeakMap<AppStore, number>()

/**
 * Resolves and hydrates `activeProject` when a workspace drawing loads:
 * 1. Local `myProjects` (`aura.projects`) bundles synchronously,
 * 2. Server `/projects` + `/drawings` (`myProjects.forDrawing` + `autoGroupDrawings`) when live API is active.
 */
export async function hydrateActiveProject(opts: {
  drawingId: string
  drawing?: Drawing
  sourceKind: 'api' | 'local' | null
  store?: AppStore
  baseUrl?: string | null
  signal?: AbortSignal
}): Promise<ProjectBundle | null> {
  const store = opts.store ?? appStore
  const { drawingId, sourceKind, signal } = opts
  const seq = (hydrationSeq.get(store) ?? 0) + 1
  hydrationSeq.set(store, seq)
  const initialLoadId = store.getState().session.load.id
  const isStillCurrent = () =>
    !signal?.aborted &&
    hydrationSeq.get(store) === seq &&
    store.getState().session.load.id === initialLoadId

  // 1. Synchronous check against user-saved projects in localStorage (`aura.projects`).
  const saved = myProjects.forDrawing(drawingId)
  if (saved && saved.floors.length >= 2) {
    if (isStillCurrent()) store.getState().setActiveProject(saved)
    return saved
  }

  // While the data source is still booting (`sourceKind === null`), preserve any primed project until ready.
  if (sourceKind === null) {
    const primed = store.getState().activeProject
    return primed && primed.floors.length >= 2 && primed.floors.some((f) => f.drawing_id === drawingId)
      ? primed
      : null
  }

  const base = opts.baseUrl !== undefined ? opts.baseUrl : apiBase()
  if (sourceKind === 'api' && base) {
    let srvProjects: ProjectBundle[] = []
    try {
      srvProjects = await listProjects({ baseUrl: base }, { timeoutMs: 8_000, headers: myProjects.listHeaders() })
      if (srvProjects.length > 0 && !signal?.aborted) myProjects.syncFromServer(srvProjects)
    } catch {
      if (signal?.aborted) return null
      /* `/projects` optional or unreachable: fall through to `/drawings` auto-grouping */
    }

    try {
      const listing = await listDrawings({ baseUrl: base }, 8_000)
      if (!isStillCurrent()) return null
      const resolved = myProjects.forDrawing(drawingId, listing.drawings, srvProjects)
      if (resolved && resolved.floors.length >= 2) {
        store.getState().setActiveProject(resolved)
        return resolved
      }
    } catch {
      if (!isStillCurrent()) return null
      const fromSrvOnly = myProjects.forDrawing(drawingId, [], srvProjects)
      if (fromSrvOnly && fromSrvOnly.floors.length >= 2) {
        store.getState().setActiveProject(fromSrvOnly)
        return fromSrvOnly
      }
    }
  }

  if (isStillCurrent()) {
    store.getState().setActiveProject(null)
  }
  return null
}

/**
 * Steps up (`+1`, `Alt+ArrowUp`) or down (`-1`, `Alt+ArrowDown`) one floor in the active House Project bundle.
 * Returns the target floor's `drawing_id`, or `null` when not in a multi-floor project or already at the boundary.
 */
export function stepProjectFloor(store: AppStore, delta: -1 | 1): string | null {
  const s = store.getState()
  if (!s.activeProject) {
    const saved = myProjects.forDrawing(s.session.drawing.id)
    if (saved && saved.floors.length >= 2) {
      s.setActiveProject(saved)
    }
  }
  return store.getState().stepFloor(delta)
}

function switchTool(s: AppState, next: Tool): boolean {
  if (next === 'whatif') {
    const canWhatIf = s.session.drawing.rooms.some((r) => Boolean(r.rect)) && s.answererMode !== 'agent'
    if (!canWhatIf) return false
  }
  if (s.ui.tool === 'whatif' && next !== 'whatif') {
    s.cancelWhatIf()
  }
  s.setTool(next)
  return true
}

function navigateIfCrossFloorEvidence(
  store: AppStore,
  evidenceId: string | null,
  navigate?: (to: string) => void,
) {
  if (!evidenceId || !navigate) return
  const s = store.getState()
  const turn = s.turns.find((t): t is AgentTurn => t.role === 'agent' && t.id === s.ui.activeTurnId)
  const ev = turn?.response?.evidence ?? turn?.stream?.evidence
  if (!ev) return
  const item = evidenceId.startsWith('room-')
    ? ev.rooms.find((r) => `room-${r.room_id}` === evidenceId)
    : ev.boxes.find((b) => b.id === evidenceId)
  if (item?.drawing_id && item.drawing_id !== s.session.drawing.id) {
    navigate(`/d/${item.drawing_id}`)
  }
}

export interface ShortcutDispatchOptions {
  store?: AppStore
  navigate?: (to: string) => void
  focusComposer?: () => void
}

/**
 * Pure event dispatcher for AURA workspace shortcuts (EN / FR / FI / DE layout-safe).
 * Returns `true` when the keystroke matched and executed a shortcut.
 */
export function dispatchWorkspaceShortcut(e: KeyboardEvent, opts: ShortcutDispatchOptions = {}): boolean {
  const store = opts.store ?? appStore
  const focusComposer = opts.focusComposer ?? (() => useDraft.getState().focus())
  const s = store.getState()
  const { isAltGr, hasCmdCtrl, hasPlainAlt } = readShortcutModifiers(e)
  const keyLower = e.key.toLowerCase()

  // 1. Mod+K toggles the Command Switcher from anywhere (including inside Composer or CommandInput),
  //    unless an unrelated modal/menu (e.g. PluginsSheet) owns focus.
  if (
    !e.defaultPrevented &&
    !e.isComposing &&
    e.keyCode !== 229 &&
    hasCmdCtrl &&
    !hasPlainAlt &&
    !e.shiftKey &&
    (keyLower === 'k' || e.code === 'KeyK')
  ) {
    if (s.ui.commandOpen) {
      e.preventDefault()
      s.setCommandOpen(false)
      return true
    }
    if (s.ui.shortcutsOpen) {
      e.preventDefault()
      s.setCommandOpen(true)
      return true
    }
    if (overlayOpen()) return false
    e.preventDefault()
    s.setCommandOpen(true)
    return true
  }

  // 2. `?` toggles the Shortcut Help cheatsheet closed when it is already open.
  if (
    s.ui.shortcutsOpen &&
    !e.defaultPrevented &&
    !e.isComposing &&
    e.keyCode !== 229 &&
    !isEditable(e.target) &&
    !hasCmdCtrl &&
    !hasPlainAlt &&
    e.key === '?'
  ) {
    e.preventDefault()
    s.setShortcutsOpen(false)
    return true
  }

  // 3. Guard IME composition, editable text fields, sliders/canvas roles, and open menus/dialogs.
  if (shouldIgnoreShortcut(e)) return false

  // 4. Mod+Z: single-source Undo outside editable fields. Never hijack any other Cmd/Ctrl shortcut
  //    (browser zoom Ctrl++/Ctrl+-/Ctrl+0, copy Ctrl+C, reload Ctrl+R, etc.).
  if (hasCmdCtrl) {
    if (!hasPlainAlt && !e.shiftKey && keyLower === 'z') {
      if (s.undoStack.length === 0) return false
      e.preventDefault()
      s.undo()
      return true
    }
    return false
  }

  // 5. Alt+ArrowUp / Alt+ArrowDown: step up / down floors in the active House Project bundle.
  if (hasPlainAlt && !e.shiftKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
    const nextDrawingId = stepProjectFloor(store, e.key === 'ArrowUp' ? 1 : -1)
    if (!nextDrawingId) return false
    e.preventDefault()
    opts.navigate?.(`/d/${nextDrawingId}`)
    return true
  }

  // 5b. Power-user Alt chords: Alt+1..9 floors, Alt+A all floors, Alt+H history, Alt+N new chat, Alt+L library,
  //     Alt+P plugins, Alt+S read aloud, Alt+V dictate, Alt+Shift+V live voice. Letters use `e.key` (layout-aware)
  //     and fall back to `e.code` for macOS Option, which rewrites `e.key`.
  if (hasPlainAlt) {
    const digit = /^(?:Digit|Numpad)([1-9])$/.exec(e.code)
    const letter = /^[a-z]$/.test(keyLower) ? keyLower : e.code.startsWith('Key') ? e.code.slice(3).toLowerCase() : ''
    let id: PowerActionId | null = null
    // macOS Option+5/6 produce `[` / `]`: those stay evidence-step keys, not floor jumps.
    if (digit && !e.shiftKey && !'[]/?+=-'.includes(e.key)) id = `floor-${digit[1]}` as PowerActionId
    else if (letter === 'v') id = e.shiftKey ? 'live' : 'dictate'
    else if (!e.shiftKey) {
      id = ({ a: 'all-floors', h: 'history', n: 'new-chat', l: 'library', p: 'plugins', s: 'speak' } as Record<string, PowerActionId>)[letter] ?? null
    }
    if (id) {
      if (!runPowerAction(id, { store, navigate: opts.navigate })) return false
      e.preventDefault()
      return true
    }
  }

  // 5c. Shift+1..4 zoom presets (physical digit keys, so FR AZERTY's shifted digits work too).
  if (e.shiftKey && !hasPlainAlt && !isAltGr) {
    const m = /^Digit([1-4])$/.exec(e.code)
    if (m) {
      e.preventDefault()
      runPowerAction(`zoom-${[100, 200, 300, 450][Number(m[1]) - 1]}` as PowerActionId, { store })
      return true
    }
  }

  // 6. Punctuation keys: evaluated via `e.key` BEFORE `isAltGr`/`hasPlainAlt` rejection so Shift-shifted
  //    (`/`, `?`, `=`, `+`), AltGr-shifted (`[`, `]` on FI/DE/FR), and macOS Option-shifted (`[`, `]`, `/`)
  //    symbols all work natively across EN, FR, FI, and DE keyboards.
  if (e.key === '/') {
    e.preventDefault()
    focusComposer()
    return true
  }
  if (e.key === '?') {
    e.preventDefault()
    s.setShortcutsOpen(true)
    return true
  }
  if (e.key === '+' || e.key === '=') {
    e.preventDefault()
    s.zoomIn()
    return true
  }
  if (e.key === '-') {
    e.preventDefault()
    s.zoomOut()
    return true
  }
  if (e.key === ']') {
    e.preventDefault()
    const chosen = s.stepEvidence(1)
    navigateIfCrossFloorEvidence(store, chosen, opts.navigate)
    return true
  }
  if (e.key === '[') {
    e.preventDefault()
    const chosen = s.stepEvidence(-1)
    navigateIfCrossFloorEvidence(store, chosen, opts.navigate)
    return true
  }

  // 7. Reject remaining Alt / AltGr chorded keys so e.g. AltGr+2 (`²`/`@`) or AltGr+0 (`}`/`@`)
  //    never misfire physical Digit1/2/3/0 tool or zoom hotkeys.
  if (hasPlainAlt || isAltGr) return false

  // 8a. Power-user plain keys: arrows pan (Shift = fast), F fit, H peek, G / Shift+G colour groups,
  //     D filtered-out candidates, E CSV export. Arrows stay with focused widgets (radios, tabs, menus, ...).
  if (e.key.startsWith('Arrow')) {
    if (e.target instanceof Element && e.target.closest('[role=radio],[role=tab],[role=option],[role=menuitem],[role=treeitem],[role=gridcell]')) {
      return false
    }
    const dir = e.key.slice(5).toLowerCase()
    e.preventDefault()
    runPowerAction(`pan-${e.shiftKey ? 'fast-' : ''}${dir}` as PowerActionId, { store })
    return true
  }
  if (!e.shiftKey && !e.repeat && keyLower === 'f') {
    e.preventDefault()
    runPowerAction('fit', { store })
    return true
  }
  if (!e.shiftKey && !e.repeat && keyLower === 'h') {
    e.preventDefault()
    peekDownAt = performance.now()
    runPowerAction('peek', { store })
    return true
  }
  if (keyLower === 'g') {
    if (!runPowerAction(e.shiftKey ? 'group-prev' : 'group-next', { store })) return false
    e.preventDefault()
    return true
  }
  if (!e.shiftKey && !e.repeat && keyLower === 'd') {
    e.preventDefault()
    runPowerAction('dropped', { store })
    return true
  }
  if (!e.shiftKey && !e.repeat && keyLower === 'e') {
    if (!runPowerAction('export', { store })) return false
    e.preventDefault()
    return true
  }

  // 8. Tool selection (`1`/`V`, `2`/`M`, `3`/`W` — supporting FR AZERTY unshifted Digit1..3 via `e.code`).
  if (e.key === '1' || (!e.shiftKey && (e.code === 'Digit1' || keyLower === 'v'))) {
    e.preventDefault()
    switchTool(s, 'select')
    return true
  }
  if (e.key === '2' || (!e.shiftKey && (e.code === 'Digit2' || keyLower === 'm'))) {
    e.preventDefault()
    switchTool(s, 'mark')
    return true
  }
  if (e.key === '3' || (!e.shiftKey && (e.code === 'Digit3' || keyLower === 'w'))) {
    if (!switchTool(s, 'whatif')) return false
    e.preventDefault()
    return true
  }

  // 9. Fit drawing to 100 % (`0` / unshifted `Digit0` on FR AZERTY).
  if (e.key === '0' || (!e.shiftKey && e.code === 'Digit0')) {
    e.preventDefault()
    s.resetZoom()
    return true
  }

  // 10. Compare original overlay (`C` toggles on/off; `Shift+C` switches Swipe <-> Blend and turns on).
  if (keyLower === 'c') {
    if (!s.session.drawing.images.original) return false
    e.preventDefault()
    if (e.shiftKey) {
      s.setCompare({ on: true, mode: s.ui.compare.mode === 'swipe' ? 'blend' : 'swipe' })
    } else {
      s.setCompare({ on: !s.ui.compare.on })
    }
    return true
  }

  // 11. Evidence verification (`J` next, `K` previous, `X` / `Delete` reject or restore, `T` reasoning trace).
  if (!e.shiftKey && keyLower === 'j') {
    e.preventDefault()
    const chosen = s.stepEvidence(1)
    navigateIfCrossFloorEvidence(store, chosen, opts.navigate)
    return true
  }
  if (!e.shiftKey && keyLower === 'k') {
    e.preventDefault()
    const chosen = s.stepEvidence(-1)
    navigateIfCrossFloorEvidence(store, chosen, opts.navigate)
    return true
  }
  if (!e.shiftKey && (keyLower === 'x' || e.key === 'Delete')) {
    const { activeTurnId, selectedEvidenceId } = s.ui
    if (!activeTurnId || !selectedEvidenceId) return false
    e.preventDefault()
    s.toggle(activeTurnId, selectedEvidenceId)
    return true
  }
  if (!e.shiftKey && keyLower === 't') {
    e.preventDefault()
    if (!s.ui.traceOpen && !s.ui.activeTurnId) {
      const latestAgent = [...s.turns].reverse().find((turn): turn is AgentTurn => turn.role === 'agent')
      if (latestAgent) s.setActive(latestAgent.id)
    }
    s.setTraceOpen(!s.ui.traceOpen)
    return true
  }

  return false
}

/** Attaches the global workspace shortcut listener while `/d/:drawingId` is mounted. */
export function useWorkspaceShortcuts(opts: { store?: AppStore; navigate?: (to: string) => void } = {}): void {
  const routerNavigate = useNavigate()
  const store = opts.store ?? appStore
  const customNavigate = opts.navigate

  useEffect(() => {
    const navigate = customNavigate ?? ((to: string) => void routerNavigate(to))
    const onKeyDown = (e: KeyboardEvent) => {
      dispatchWorkspaceShortcut(e, { store, navigate })
    }
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== 'h' || peekDownAt === 0) return
      const held = performance.now() - peekDownAt
      peekDownAt = 0
      if (held > PEEK_HOLD_MS) runPowerAction('peek', { store })
    }
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
    }
  }, [store, customNavigate, routerNavigate])
}

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { createSource } from '@/api/source'
import { autoGroupDrawings } from '@/api/library'
import { AppShell, SPLIT_QUERY } from '@/components/shell/AppShell'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'
import { bundledDrawing } from '@/data/drawing'
import { ChatPanel } from '@/features/chat/ChatPanel'
import { useDraft } from '@/features/chat/draft'
import { MOBILE_QUERY } from '@/features/plan/shared'
import { TracePanel } from '@/features/trace/TracePanel'
import { uiLang } from '@/i18n/uiLang'
import { appStore, createAppStore, type AgentTurn, type AppStore } from '@/store'
import { CommandPaletteDialog } from './CommandPaletteDialog'
import { ShortcutHelpDialog } from './ShortcutHelpDialog'
import {
  dispatchWorkspaceShortcut,
  hydrateActiveProject,
  inferFloorOrderAndLabel,
  useWorkspaceShortcuts,
  type ProjectBundle,
} from './useWorkspaceShortcuts'

// React 19 act environment flag for Vitest + jsdom
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

beforeAll(() => {
  if (typeof globalThis.ResizeObserver === 'undefined') {
    globalThis.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {}
  }
  if (!window.matchMedia) {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: (query: string) => ({
        matches: query.includes('min-width'),
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    })
  }
})

const makeStore = (drawing = bundledDrawing): AppStore =>
  createAppStore({ source: createSource({ baseUrl: null }), drawing })

function keyEvent(
  key: string,
  init: KeyboardEventInit & { altGraph?: boolean; keyCodeOverride?: number } = {},
  target: EventTarget = document.body,
): KeyboardEvent {
  const { altGraph, keyCodeOverride, ...rest } = init
  const ev = new KeyboardEvent('keydown', {
    key,
    bubbles: true,
    cancelable: true,
    ...rest,
  })
  Object.defineProperty(ev, 'target', { value: target })
  if (altGraph) {
    ev.getModifierState = (mod: string) => mod === 'AltGraph'
  }
  if (keyCodeOverride !== undefined) {
    Object.defineProperty(ev, 'keyCode', { value: keyCodeOverride })
  }
  return ev
}

const mountedRoots: { root: Root; container: HTMLDivElement }[] = []

function mount(node: React.ReactNode) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => {
    root.render(node)
  })
  mountedRoots.push({ root, container })
  return { container, root }
}

beforeEach(() => {
  localStorage.clear()
  uiLang.set('en')
  useDraft.setState({ text: '', focusTick: 0, select: null })
})

afterEach(() => {
  for (const { root, container } of mountedRoots.splice(0)) {
    act(() => {
      root.unmount()
    })
    container.remove()
  }
  document.body.innerHTML = ''
})

describe('useWorkspaceShortcuts — EN / FR / FI / DE layouts & guards', () => {
  it('handles EN QWERTY navigation, tools, compare, zoom, evidence, trace, undo, and help', async () => {
    const store = makeStore()
    const turnId = await store.getState().ask('How many windows are drawn on this floor plan?')
    let focused = 0
    const opts = { store, focusComposer: () => (focused += 1) }

    // `/` focuses composer
    const slash = keyEvent('/', { code: 'Slash' })
    expect(dispatchWorkspaceShortcut(slash, opts)).toBe(true)
    expect(slash.defaultPrevented).toBe(true)
    expect(focused).toBe(1)

    // `Mod+K` toggles command palette open and closed
    expect(dispatchWorkspaceShortcut(keyEvent('k', { code: 'KeyK', ctrlKey: true }), opts)).toBe(true)
    expect(store.getState().ui.commandOpen).toBe(true)
    expect(dispatchWorkspaceShortcut(keyEvent('k', { code: 'KeyK', metaKey: true }), opts)).toBe(true)
    expect(store.getState().ui.commandOpen).toBe(false)

    // `?` toggles shortcut help open and closed
    expect(dispatchWorkspaceShortcut(keyEvent('?', { code: 'Slash', shiftKey: true }), opts)).toBe(true)
    expect(store.getState().ui.shortcutsOpen).toBe(true)
    expect(dispatchWorkspaceShortcut(keyEvent('?', { code: 'Slash', shiftKey: true }), opts)).toBe(true)
    expect(store.getState().ui.shortcutsOpen).toBe(false)

    // Tools: `2`/`M`, `3`/`W`, `1`/`V` (and cancelling What-If when switching away)
    expect(dispatchWorkspaceShortcut(keyEvent('2', { code: 'Digit2' }), opts)).toBe(true)
    expect(store.getState().ui.tool).toBe('mark')
    expect(dispatchWorkspaceShortcut(keyEvent('w', { code: 'KeyW' }), opts)).toBe(true)
    expect(store.getState().ui.tool).toBe('whatif')
    const rectRoom = bundledDrawing.rooms.find((r) => r.rect)!
    store.getState().startWhatIf(rectRoom.room_id)
    expect(store.getState().ui.whatIf.roomId).toBe(rectRoom.room_id)
    expect(dispatchWorkspaceShortcut(keyEvent('v', { code: 'KeyV' }), opts)).toBe(true)
    expect(store.getState().ui.tool).toBe('select')
    expect(store.getState().ui.whatIf.roomId).toBeNull()

    // Compare: `C` toggles on/off; `Shift+C` switches Swipe <-> Blend
    expect(dispatchWorkspaceShortcut(keyEvent('c', { code: 'KeyC' }), opts)).toBe(true)
    expect(store.getState().ui.compare).toMatchObject({ on: true, mode: 'swipe' })
    expect(dispatchWorkspaceShortcut(keyEvent('C', { code: 'KeyC', shiftKey: true }), opts)).toBe(true)
    expect(store.getState().ui.compare).toMatchObject({ on: true, mode: 'blend' })
    expect(dispatchWorkspaceShortcut(keyEvent('c', { code: 'KeyC' }), opts)).toBe(true)
    expect(store.getState().ui.compare.on).toBe(false)

    // Zoom: `+`, `=`, `-`, `0`
    expect(dispatchWorkspaceShortcut(keyEvent('+', { code: 'Equal', shiftKey: true }), opts)).toBe(true)
    expect(store.getState().ui.zoom).toBeGreaterThan(100)
    expect(dispatchWorkspaceShortcut(keyEvent('=', { code: 'Equal' }), opts)).toBe(true)
    const zoomed = store.getState().ui.zoom
    expect(dispatchWorkspaceShortcut(keyEvent('-', { code: 'Minus' }), opts)).toBe(true)
    expect(store.getState().ui.zoom).toBeLessThan(zoomed)
    expect(dispatchWorkspaceShortcut(keyEvent('0', { code: 'Digit0' }), opts)).toBe(true)
    expect(store.getState().ui.zoom).toBe(100)

    // Evidence stepping (`J` / `]` / `K` / `[`), toggling (`X` / `Delete`), and Undo (`Mod+Z`)
    expect(dispatchWorkspaceShortcut(keyEvent('j', { code: 'KeyJ' }), opts)).toBe(true)
    const firstId = store.getState().ui.selectedEvidenceId!
    expect(firstId).toBeTruthy()
    expect(dispatchWorkspaceShortcut(keyEvent(']', { code: 'BracketRight' }), opts)).toBe(true)
    const secondId = store.getState().ui.selectedEvidenceId!
    expect(secondId).not.toBe(firstId)
    expect(dispatchWorkspaceShortcut(keyEvent('[', { code: 'BracketLeft' }), opts)).toBe(true)
    expect(store.getState().ui.selectedEvidenceId).toBe(firstId)

    expect(dispatchWorkspaceShortcut(keyEvent('x', { code: 'KeyX' }), opts)).toBe(true)
    const turnAfterReject = store.getState().turns.find((t) => t.id === turnId) as AgentTurn
    expect(turnAfterReject.toggles.rejected[firstId]).toBe(true)

    expect(dispatchWorkspaceShortcut(keyEvent('Delete', { code: 'Delete' }), opts)).toBe(true)
    const turnAfterRestore = store.getState().turns.find((t) => t.id === turnId) as AgentTurn
    expect(turnAfterRestore.toggles.rejected[firstId]).toBeUndefined()

    // Reject again (`X` -> `Delete` -> `X` pushes 3 entries) then Mod+Z pops 1 entry and restores `firstId`
    dispatchWorkspaceShortcut(keyEvent('x', { code: 'KeyX' }), opts)
    const stackBeforeUndo = store.getState().undoStack.length
    expect(stackBeforeUndo).toBe(3)
    expect(dispatchWorkspaceShortcut(keyEvent('z', { code: 'KeyZ', ctrlKey: true }), opts)).toBe(true)
    expect(store.getState().undoStack.length).toBe(stackBeforeUndo - 1)
    const turnAfterUndo = store.getState().turns.find((t) => t.id === turnId) as AgentTurn
    expect(turnAfterUndo.toggles.rejected[firstId]).toBeUndefined()

    // `T` toggles Reasoning Trace
    expect(dispatchWorkspaceShortcut(keyEvent('t', { code: 'KeyT' }), opts)).toBe(true)
    expect(store.getState().ui.traceOpen).toBe(true)
    expect(dispatchWorkspaceShortcut(keyEvent('t', { code: 'KeyT' }), opts)).toBe(true)
    expect(store.getState().ui.traceOpen).toBe(false)
  })

  it('supports FR AZERTY unshifted digits, Shift punctuation, and AltGr brackets without false positives', async () => {
    const store = makeStore()
    await store.getState().ask('How many windows are drawn on this floor plan?')
    let focused = 0
    const opts = { store, focusComposer: () => (focused += 1) }

    // FR AZERTY unshifted Digit2 (`é`), Digit3 (`"`), Digit1 (`&`), Digit0 (`à`)
    expect(dispatchWorkspaceShortcut(keyEvent('é', { code: 'Digit2' }), opts)).toBe(true)
    expect(store.getState().ui.tool).toBe('mark')
    expect(dispatchWorkspaceShortcut(keyEvent('"', { code: 'Digit3' }), opts)).toBe(true)
    expect(store.getState().ui.tool).toBe('whatif')
    expect(dispatchWorkspaceShortcut(keyEvent('&', { code: 'Digit1' }), opts)).toBe(true)
    expect(store.getState().ui.tool).toBe('select')

    store.getState().setZoom(260)
    expect(dispatchWorkspaceShortcut(keyEvent('à', { code: 'Digit0' }), opts)).toBe(true)
    expect(store.getState().ui.zoom).toBe(100)

    // FR AZERTY Shift+`:` -> `/` and Shift+`,` -> `?`
    expect(dispatchWorkspaceShortcut(keyEvent('/', { code: 'Period', shiftKey: true }), opts)).toBe(true)
    expect(focused).toBe(1)
    expect(dispatchWorkspaceShortcut(keyEvent('?', { code: 'KeyM', shiftKey: true }), opts)).toBe(true)
    expect(store.getState().ui.shortcutsOpen).toBe(true)
    store.getState().setShortcutsOpen(false)

    // FR AZERTY AltGr+5 -> `[` and AltGr+`)` -> `]`
    expect(
      dispatchWorkspaceShortcut(keyEvent(']', { code: 'Minus', ctrlKey: true, altKey: true }), opts),
    ).toBe(true)
    const first = store.getState().ui.selectedEvidenceId
    expect(first).toBeTruthy()
    expect(
      dispatchWorkspaceShortcut(keyEvent('[', { code: 'Digit5', altGraph: true }), opts),
    ).toBe(true)

    // FR AZERTY AltGr+2 (`~`, code `Digit2`) MUST NOT misfire Mark tool!
    store.getState().setTool('select')
    expect(
      dispatchWorkspaceShortcut(keyEvent('~', { code: 'Digit2', ctrlKey: true, altKey: true }), opts),
    ).toBe(false)
    expect(store.getState().ui.tool).toBe('select')
  })

  it('supports FI / DE Shift+7 (`/`), Shift+0 (`=`), AltGr+8/9 (`[`/`]`), and macOS Option brackets', async () => {
    const store = makeStore()
    await store.getState().ask('How many windows are drawn on this floor plan?')
    let focused = 0
    const opts = { store, focusComposer: () => (focused += 1) }

    // FI/DE Shift+7 -> `/`
    expect(dispatchWorkspaceShortcut(keyEvent('/', { code: 'Digit7', shiftKey: true }), opts)).toBe(true)
    expect(focused).toBe(1)

    // FI/DE Shift+0 -> `=` (MUST zoom in, NOT reset zoom via Digit0!)
    expect(store.getState().ui.zoom).toBe(100)
    expect(dispatchWorkspaceShortcut(keyEvent('=', { code: 'Digit0', shiftKey: true }), opts)).toBe(true)
    expect(store.getState().ui.zoom).toBe(140)

    // FI/DE Windows/Linux AltGr+8 (`ctrlKey + altKey`) -> `[` and AltGr+9 -> `]`
    expect(
      dispatchWorkspaceShortcut(keyEvent(']', { code: 'Digit9', ctrlKey: true, altKey: true }), opts),
    ).toBe(true)
    const ev0 = store.getState().ui.selectedEvidenceId
    expect(
      dispatchWorkspaceShortcut(keyEvent(']', { code: 'Digit9', altGraph: true }), opts),
    ).toBe(true)
    expect(store.getState().ui.selectedEvidenceId).not.toBe(ev0)
    expect(
      dispatchWorkspaceShortcut(keyEvent('[', { code: 'Digit8', ctrlKey: true, altKey: true }), opts),
    ).toBe(true)
    expect(store.getState().ui.selectedEvidenceId).toBe(ev0)

    // FI/DE AltGr+2 (`²`) and AltGr+0 (`}`) MUST NOT trigger Digit2 or Digit0
    store.getState().setTool('select')
    expect(
      dispatchWorkspaceShortcut(keyEvent('²', { code: 'Digit2', ctrlKey: true, altKey: true }), opts),
    ).toBe(false)
    expect(store.getState().ui.tool).toBe('select')
    expect(
      dispatchWorkspaceShortcut(keyEvent('}', { code: 'Digit0', ctrlKey: true, altKey: true }), opts),
    ).toBe(false)
    expect(store.getState().ui.zoom).toBe(140)

    // macOS DE Option+5 (`[`) and Option+6 (`]`)
    expect(dispatchWorkspaceShortcut(keyEvent(']', { code: 'Digit6', altKey: true }), opts)).toBe(true)
    expect(dispatchWorkspaceShortcut(keyEvent('[', { code: 'Digit5', altKey: true }), opts)).toBe(true)
  })

  it('never hijacks browser Ctrl/Cmd shortcuts and respects IME, editable fields, sliders, and overlays', async () => {
    const store = makeStore()
    await store.getState().ask('How many windows are drawn on this floor plan?')
    store.getState().stepEvidence(1)
    const selectedId = store.getState().ui.selectedEvidenceId!
    const turnId = store.getState().ui.activeTurnId!
    store.getState().toggle(turnId, selectedId)
    expect(store.getState().undoStack.length).toBe(1)

    // Browser shortcuts: Ctrl++, Ctrl+-, Ctrl+0, Ctrl+C
    for (const ev of [
      keyEvent('+', { ctrlKey: true, shiftKey: true }),
      keyEvent('-', { ctrlKey: true }),
      keyEvent('0', { ctrlKey: true, code: 'Digit0' }),
      keyEvent('c', { ctrlKey: true, code: 'KeyC' }),
    ]) {
      expect(dispatchWorkspaceShortcut(ev, { store })).toBe(false)
      expect(ev.defaultPrevented).toBe(false)
    }

    // IME composition (`isComposing` & `keyCode === 229`) blocks even Mod+K
    expect(dispatchWorkspaceShortcut(keyEvent('k', { ctrlKey: true, isComposing: true }), { store })).toBe(false)
    expect(dispatchWorkspaceShortcut(keyEvent('k', { ctrlKey: true, keyCodeOverride: 229 }), { store })).toBe(false)
    expect(store.getState().ui.commandOpen).toBe(false)

    // Inside <textarea>: Ctrl+Z and single-key hotkeys are ignored, while Mod+K STILL toggles Command Switcher!
    const textarea = document.createElement('textarea')
    document.body.appendChild(textarea)
    const ctrlZInInput = keyEvent('z', { ctrlKey: true, code: 'KeyZ' }, textarea)
    expect(dispatchWorkspaceShortcut(ctrlZInInput, { store })).toBe(false)
    expect(ctrlZInInput.defaultPrevented).toBe(false)
    expect(store.getState().undoStack.length).toBe(1)
    expect(dispatchWorkspaceShortcut(keyEvent('2', { code: 'Digit2' }, textarea), { store })).toBe(false)

    const modKInInput = keyEvent('k', { ctrlKey: true, code: 'KeyK' }, textarea)
    expect(dispatchWorkspaceShortcut(modKInInput, { store })).toBe(true)
    expect(store.getState().ui.commandOpen).toBe(true)
    store.getState().setCommandOpen(false)

    // Inside [role="slider"]: single-key shortcuts are ignored
    const slider = document.createElement('div')
    slider.setAttribute('role', 'slider')
    document.body.appendChild(slider)
    expect(dispatchWorkspaceShortcut(keyEvent('+', { code: 'Equal' }, slider), { store })).toBe(false)

    // Tooltip popper does NOT block shortcuts, whereas an open [role="menu"] DOES block
    const tooltipPopper = document.createElement('div')
    tooltipPopper.setAttribute('data-radix-popper-content-wrapper', '')
    const tip = document.createElement('div')
    tip.setAttribute('role', 'tooltip')
    tooltipPopper.appendChild(tip)
    document.body.appendChild(tooltipPopper)
    expect(dispatchWorkspaceShortcut(keyEvent('2', { code: 'Digit2' }), { store })).toBe(true)
    expect(store.getState().ui.tool).toBe('mark')

    const menu = document.createElement('div')
    menu.setAttribute('role', 'menu')
    document.body.appendChild(menu)
    expect(dispatchWorkspaceShortcut(keyEvent('1', { code: 'Digit1' }), { store })).toBe(false)
    expect(dispatchWorkspaceShortcut(keyEvent('k', { ctrlKey: true, code: 'KeyK' }), { store })).toBe(false)
  })

  it('guards What-If shortcut (`3`/`W`) when rooms lack resizable rects or answererMode is agent', () => {
    const noRectDrawing = {
      ...bundledDrawing,
      rooms: bundledDrawing.rooms.map((r) => ({ ...r, rect: undefined })),
    }
    const s1 = makeStore(noRectDrawing)
    expect(dispatchWorkspaceShortcut(keyEvent('3', { code: 'Digit3' }), { store: s1 })).toBe(false)
    expect(s1.getState().ui.tool).toBe('select')

    const s2 = makeStore()
    s2.getState().setAnswererMode('agent')
    expect(dispatchWorkspaceShortcut(keyEvent('w', { code: 'KeyW' }), { store: s2 })).toBe(false)
    expect(s2.getState().ui.tool).toBe('select')
  })

  it('pops undoStack exactly once on Ctrl+Z when both ChatPanel and useWorkspaceShortcuts are mounted', async () => {
    await appStore.getState().loadDrawing(bundledDrawing.id)
    appStore.getState().newConversation()
    const turnId = await appStore.getState().ask('How many windows are drawn on this floor plan?')
    const initialTurn = appStore.getState().turns.find((t) => t.id === turnId) as AgentTurn
    const boxA = initialTurn.response!.evidence.boxes[0]!.id
    const boxB = initialTurn.response!.evidence.boxes[1]!.id
    appStore.getState().toggle(turnId, boxA)
    appStore.getState().toggle(turnId, boxB)
    expect(appStore.getState().undoStack.length).toBe(2)

    function Harness() {
      useWorkspaceShortcuts()
      return <ChatPanel />
    }

    mount(
      <MemoryRouter>
        <Harness />
      </MemoryRouter>,
    )

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', code: 'KeyZ', ctrlKey: true, bubbles: true, cancelable: true }))
    })

    expect(appStore.getState().undoStack.length).toBe(1)
    const turn = appStore.getState().turns.find((t) => t.id === turnId) as AgentTurn
    expect(turn.toggles.rejected[boxB]).toBeUndefined()
    expect(turn.toggles.rejected[boxA]).toBe(true)
  })
})

describe('House / Project bundle hydration & Alt+ArrowUp / Alt+ArrowDown floor stepping', () => {
  it('infers architectural floor order and steps up/down floors with boundary clamping', async () => {
    expect(inferFloorOrderAndLabel('Haus A — Kellergeschoss (KG)')).toEqual({ order: -1, label: 'KG' })
    expect(inferFloorOrderAndLabel('Haus A — Erdgeschoss')).toEqual({ order: 0, label: 'EG' })
    expect(inferFloorOrderAndLabel('Haus A — 1. Obergeschoss')).toEqual({ order: 1, label: '1. OG' })
    expect(inferFloorOrderAndLabel('Haus A — Dachgeschoss')).toEqual({ order: 99, label: 'DG' })

    const bundle: ProjectBundle = {
      id: 'proj-villa',
      title: 'Villa Warmond',
      floors: [
        { drawing_id: 'floor-1og', label: '1. OG', order: 1 },
        { drawing_id: 'swiss-46639', label: 'EG', order: 0 },
        { drawing_id: 'floor-kg', label: 'KG', order: -1 },
      ],
    }
    localStorage.setItem('aura.projects', JSON.stringify([bundle]))

    const store = makeStore()
    await hydrateActiveProject({ drawingId: 'swiss-46639', sourceKind: 'local', store })
    expect(store.getState().activeProject?.id).toBe('proj-villa')

    const visited: string[] = []
    const navigate = (to: string) => visited.push(to)

    // Currently on `swiss-46639` (EG, order 0): Alt+ArrowUp -> `floor-1og`, Alt+ArrowDown -> `floor-kg`
    expect(dispatchWorkspaceShortcut(keyEvent('ArrowUp', { altKey: true }), { store, navigate })).toBe(true)
    expect(visited).toEqual(['/d/floor-1og'])

    expect(dispatchWorkspaceShortcut(keyEvent('ArrowDown', { altKey: true }), { store, navigate })).toBe(true)
    expect(visited).toEqual(['/d/floor-1og', '/d/floor-kg'])

    // At top floor (`floor-1og`), Alt+ArrowUp is a no-op
    const topStore = makeStore({ ...bundledDrawing, id: 'floor-1og' })
    topStore.getState().setActiveProject(bundle)
    const upAtTop = keyEvent('ArrowUp', { altKey: true })
    expect(dispatchWorkspaceShortcut(upAtTop, { store: topStore, navigate })).toBe(false)
    expect(upAtTop.defaultPrevented).toBe(false)

    // Navigating to an unbundled standalone drawing clears activeProject
    await hydrateActiveProject({ drawingId: 'standalone-999', sourceKind: 'local', store })
    expect(store.getState().activeProject).toBeNull()
  })

  it('hydrates auto-grouped sibling IFC storeys via live API listing', async () => {
    const groups = autoGroupDrawings([
      {
        id: 'ifc-duplex--level-2',
        title: 'Duplex House — Level 2',
        kind: 'ifc',
        status: 'ready',
        thumbnail: '/a.svg',
        credit: bundledDrawing.credit,
      },
      {
        id: 'ifc-duplex--ground-floor',
        title: 'Duplex House — Ground Floor',
        kind: 'ifc',
        status: 'ready',
        thumbnail: '/b.svg',
        credit: bundledDrawing.credit,
      },
      {
        id: 'ifc-duplex--level-1',
        title: 'Duplex House — Level 1',
        kind: 'ifc',
        status: 'ready',
        thumbnail: '/c.svg',
        credit: bundledDrawing.credit,
      },
    ])
    expect(groups).toHaveLength(1)
    expect(groups[0]?.floors.map((f) => f.drawing_id)).toEqual([
      'ifc-duplex--ground-floor',
      'ifc-duplex--level-1',
      'ifc-duplex--level-2',
    ])
  })
})

describe('0ms CommandPaletteDialog, ShortcutHelpDialog, AppShell & TracePanel a11y', () => {
  it('renders CommandPaletteDialog & ShortcutHelpDialog with 0ms classes, 40px close button, and executes commands', () => {
    const store = makeStore()
    const bundle: ProjectBundle = {
      id: 'proj-1',
      title: 'Haus Zürich',
      floors: [
        { drawing_id: 'swiss-46639', label: 'EG', order: 0 },
        { drawing_id: 'swiss-46640', label: '1. OG', order: 1 },
      ],
    }
    store.getState().setActiveProject(bundle)
    store.getState().setCommandOpen(true)

    const navigated: string[] = []
    mount(
      <MemoryRouter>
        <CommandPaletteDialog store={store} navigate={(to) => navigated.push(to)} />
        <ShortcutHelpDialog store={store} />
      </MemoryRouter>,
    )

    const cmdDialog = document.querySelector('[data-command-palette]')!
    expect(cmdDialog).toBeTruthy()
    expect(cmdDialog.className).toContain('duration-0')
    expect(cmdDialog.className).toContain('data-open:animate-none')
    expect(cmdDialog.className).toContain('data-closed:animate-none')

    // Click "Match all floors" in the active House Project group
    const items = [...document.querySelectorAll<HTMLElement>('[data-slot="command-item"]')]
    const matchFloorsItem = items.find((el) => el.textContent?.includes('Match all floors'))
    expect(matchFloorsItem).toBeTruthy()
    act(() => {
      matchFloorsItem!.click()
    })
    expect(store.getState().crossPlanScope).toBe(true)
    expect(store.getState().ui.commandOpen).toBe(false)

    // Open ShortcutHelpDialog and verify 0ms animation classes + 40px `size-10` close button
    act(() => {
      store.getState().setShortcutsOpen(true)
    })
    const helpDialog = document.querySelector('[data-shortcut-help]')!
    expect(helpDialog).toBeTruthy()
    expect(helpDialog.className).toContain('duration-0')
    expect(helpDialog.className).toContain('data-open:animate-none')
    expect(helpDialog.className).toContain('data-closed:animate-none')
    expect(helpDialog.querySelectorAll('[data-slot="kbd"]').length).toBeGreaterThan(8)
    const closeBtn = helpDialog.querySelector('button[aria-label="Close"]')!
    expect(closeBtn.className).toContain('size-10')
    expect(closeBtn.className).not.toContain('size-8')
  })

  it('wires 768px split queries, <main id="main"> + skip-link, 40px ResizableHandle, and conditional inert chat', () => {
    expect(SPLIT_QUERY).toBe('(min-width: 768px)')
    expect(MOBILE_QUERY).toBe('(max-width: 767px)')

    // When sidebarOverlay={<div />} stays mounted (like <SidebarOverlay />) with sidebarOverlayOpen={false},
    // [data-slot="chat"] MUST NOT be inert!
    const { root, container } = mount(
      <MemoryRouter>
        <AppShell
          stage={<div>Stage</div>}
          chat={<button type="button">Chat item</button>}
          sidebarOverlay={<div />}
          sidebarOverlayOpen={false}
        />
      </MemoryRouter>,
    )

    const skipLink = container.querySelector('a[href="#main"]')
    expect(skipLink?.textContent).toBe('Skip to drawing')
    const main = container.querySelector('main#main')
    expect(main).toBeTruthy()
    expect(main?.getAttribute('tabindex')).toBe('-1')
    const stageSlot = container.querySelector('[data-slot="stage"]')!
    expect(stageSlot.className).toContain('min-h-36')
    expect(stageSlot.className).toContain('overflow-hidden')
    const handle = container.querySelector('[data-slot="resizable-handle"]')
    if (handle) {
      expect(handle.className).toContain('after:w-10')
    } else {
      const h = mount(
        <ResizablePanelGroup orientation="horizontal">
          <ResizablePanel>A</ResizablePanel>
          <ResizableHandle withHandle />
          <ResizablePanel>B</ResizablePanel>
        </ResizablePanelGroup>,
      )
      expect(h.container.querySelector('[data-slot="resizable-handle"]')?.className).toContain('after:w-10')
    }

    const chatSlot = container.querySelector('[data-slot="chat"]')!
    expect(chatSlot.hasAttribute('inert')).toBe(false)

    // Flipping sidebarOverlayOpen={true} marks [data-slot="chat"] inert
    act(() => {
      root.render(
        <MemoryRouter>
          <AppShell
            stage={<div>Stage</div>}
            chat={<button type="button">Chat item</button>}
            sidebarOverlay={<div>Trace</div>}
            sidebarOverlayOpen={true}
          />
        </MemoryRouter>,
      )
    })
    expect(container.querySelector('[data-slot="chat"]')?.hasAttribute('inert')).toBe(true)
  })

  it('restores focus via locale-independent useDraft.getState().focus() when TracePanel closes without opener', async () => {
    uiLang.set('fr')
    await appStore.getState().loadDrawing(bundledDrawing.id)
    appStore.getState().newConversation()
    await appStore.getState().ask('Combien de fenêtres ?')
    appStore.getState().setTraceOpen(true)

    const rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
      cb(0)
      return 1
    })

    try {
      const { container } = mount(<TracePanel />)
      const beforeTick = useDraft.getState().focusTick
      const backBtn = container.querySelector<HTMLButtonElement>('button[data-trace-back]')!
      expect(backBtn).toBeTruthy()
      act(() => {
        backBtn.click()
      })
      expect(appStore.getState().ui.traceOpen).toBe(false)
      expect(useDraft.getState().focusTick).toBe(beforeTick + 1)
    } finally {
      rafSpy.mockRestore()
    }
  })
})

describe('Expanded power-user keybinds', () => {
  const bundle: ProjectBundle = {
    id: 'proj-power',
    title: 'Power House',
    floors: [
      { drawing_id: 'p-1', label: 'EG', order: 0 },
      { drawing_id: 'p-2', label: '1. OG', order: 1 },
    ],
  }

  it('pans, fits, snaps zoom, and toggles peek / dropped via stage commands', () => {
    const store = makeStore()
    const seen: { type: string; dx?: number; dy?: number }[] = []
    const onCmd = (e: Event) => seen.push((e as CustomEvent).detail)
    window.addEventListener('aura:stage-command', onCmd)
    try {
      expect(dispatchWorkspaceShortcut(keyEvent('ArrowRight'), { store })).toBe(true)
      expect(dispatchWorkspaceShortcut(keyEvent('ArrowUp', { shiftKey: true }), { store })).toBe(true)
      expect(dispatchWorkspaceShortcut(keyEvent('f'), { store })).toBe(true)
      expect(dispatchWorkspaceShortcut(keyEvent('h'), { store })).toBe(true)
      expect(dispatchWorkspaceShortcut(keyEvent('d'), { store })).toBe(true)
      expect(seen).toEqual([
        { type: 'pan', dx: 80, dy: 0 },
        { type: 'pan', dx: 0, dy: -240 },
        { type: 'fit' },
        { type: 'peek' },
        { type: 'dropped' },
      ])
      // Shift+2 (US `@`, AZERTY `2`) snaps to 200 %; Shift+4 to 450 %.
      expect(dispatchWorkspaceShortcut(keyEvent('@', { shiftKey: true, code: 'Digit2' }), { store })).toBe(true)
      expect(store.getState().ui.zoom).toBe(200)
      dispatchWorkspaceShortcut(keyEvent('$', { shiftKey: true, code: 'Digit4' }), { store })
      expect(store.getState().ui.zoom).toBe(450)
      // Arrows stay with editable fields.
      const input = document.createElement('input')
      document.body.append(input)
      expect(dispatchWorkspaceShortcut(keyEvent('ArrowLeft', {}, input), { store })).toBe(false)
      input.remove()
    } finally {
      window.removeEventListener('aura:stage-command', onCmd)
    }
  })

  it('jumps floors with Alt+digit, toggles all-floors scope, and dispatches voice / history events', () => {
    const store = makeStore({ ...bundledDrawing, id: 'p-1' })
    store.getState().setActiveProject(bundle)
    const visited: string[] = []
    const navigate = (to: string) => visited.push(to)
    expect(dispatchWorkspaceShortcut(keyEvent('2', { altKey: true, code: 'Digit2' }), { store, navigate })).toBe(true)
    expect(visited).toEqual(['/d/p-2'])
    expect(dispatchWorkspaceShortcut(keyEvent('9', { altKey: true, code: 'Digit9' }), { store, navigate })).toBe(false)
    expect(dispatchWorkspaceShortcut(keyEvent('a', { altKey: true }), { store, navigate })).toBe(true)
    expect(store.getState().crossPlanScope).toBe(true)
    expect(dispatchWorkspaceShortcut(keyEvent('l', { altKey: true }), { store, navigate })).toBe(true)
    expect(visited.at(-1)).toBe('/app')
    expect(dispatchWorkspaceShortcut(keyEvent('p', { altKey: true }), { store, navigate })).toBe(true)
    expect(store.getState().ui.pluginsOpen).toBe(true)

    const events: string[] = []
    const names = ['aura:open-history', 'aura:toggle-dictation', 'aura:toggle-speak', 'aura:toggle-live-voice']
    const on = (e: Event) => events.push(e.type)
    names.forEach((n) => window.addEventListener(n, on))
    try {
      dispatchWorkspaceShortcut(keyEvent('h', { altKey: true }), { store, navigate })
      dispatchWorkspaceShortcut(keyEvent('v', { altKey: true }), { store, navigate })
      dispatchWorkspaceShortcut(keyEvent('s', { altKey: true }), { store, navigate })
      dispatchWorkspaceShortcut(keyEvent('V', { altKey: true, shiftKey: true }), { store, navigate })
      expect(events).toEqual(names)
    } finally {
      names.forEach((n) => window.removeEventListener(n, on))
    }
    // AltGr chords never fire Alt shortcuts.
    expect(dispatchWorkspaceShortcut(keyEvent('h', { altKey: true, altGraph: true }), { store, navigate })).toBe(false)
  })
})

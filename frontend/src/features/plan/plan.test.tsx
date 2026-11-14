import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AskResponse, Box } from '@/api/contract'
import { TooltipProvider } from '@/components/ui/tooltip'
import { bundledDrawing } from '@/data/drawing'
import { emptyToggles } from '@/lib/answer'
import { appStore, type AgentTurn, type StreamState } from '@/store'
import { PlanStage } from './PlanStage'
import { PlanToolbar } from './PlanToolbar'
import {
  boxIoU,
  diffDroppedBoxes,
  extractAgentTelemetry,
  parseSpotHash,
  resolveGroupStyle,
  type EvidenceGroupLike,
  type EvidenceLinkLike,
  type GroupedBox,
  type GroupedRoom,
  type ProjectBundleLike,
} from './badges'

// Enable React 19 act() environment in jsdom.
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let reducedMotionFlag = false
let container: HTMLDivElement
let root: Root | null = null
const initialState = appStore.getState()

function installBrowserMocks() {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string): MediaQueryList => ({
      matches: query.includes('prefers-reduced-motion: reduce') ? reducedMotionFlag : false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })

  class MockResizeObserver {
    private cb: ResizeObserverCallback
    constructor(cb: ResizeObserverCallback) {
      this.cb = cb
    }
    observe() {
      this.cb([], this as unknown as ResizeObserver)
    }
    unobserve() {}
    disconnect() {}
  }
  Object.defineProperty(globalThis, 'ResizeObserver', {
    writable: true,
    configurable: true,
    value: MockResizeObserver,
  })

  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    get() {
      return 960
    },
  })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get() {
      return 720
    },
  })
  HTMLElement.prototype.scrollTo = vi.fn()
}

function makeWindowBox(idx: number, x: number, y: number, extra: Partial<GroupedBox> = {}): GroupedBox {
  return {
    id: `win-${idx}`,
    subject: 'window',
    x,
    y,
    w: 4,
    h: 2,
    ...extra,
  }
}

function makeCountResponse(
  boxes: GroupedBox[],
  extraEvidence: {
    rooms?: GroupedRoom[]
    groups?: EvidenceGroupLike[]
    links?: EvidenceLinkLike[]
  } = {},
): AskResponse {
  return {
    contract_version: '1.2',
    answer: {
      kind: 'count',
      value: boxes.length,
      subject: 'window',
    },
    evidence: {
      boxes: boxes as Box[],
      rooms: extraEvidence.rooms ?? [],
      ...(extraEvidence.groups ? { groups: extraEvidence.groups } : {}),
      ...(extraEvidence.links ? { links: extraEvidence.links } : {}),
    } as AskResponse['evidence'],
    detected_lang: 'en',
    canonical_en: 'Show all north windows',
    scope: { kind: 'floor' },
    method: 'cad_geometry',
    trace: [],
    calls: 0,
    cost_usd: 0,
    latency_ms: 12,
    source: 'local',
  }
}

function renderUi(node: React.ReactNode) {
  act(() => {
    root = createRoot(container)
    root.render(node)
  })
}

beforeEach(() => {
  reducedMotionFlag = false
  window.location.hash = ''
  localStorage.clear()
  installBrowserMocks()
  container = document.createElement('div')
  document.body.appendChild(container)
  appStore.setState(
    {
      ...initialState,
      session: {
        ...initialState.session,
        drawing: bundledDrawing,
        lang: 'en',
        answererMode: 'hybrid',
      },
      answererMode: 'hybrid',
      turns: [],
      undoStack: [],
      ui: {
        ...initialState.ui,
        activeTurnId: null,
        selectedEvidenceId: null,
        hoveredEvidenceIds: [],
        tool: 'select',
        zoom: 100,
        zoomIndex: 0,
        shortcutsOpen: false,
        marks: [],
        attachedMarkId: null,
      },
    },
    true,
  )
})

afterEach(() => {
  if (root) {
    act(() => {
      root?.unmount()
    })
    root = null
  }
  container.remove()
  vi.useRealTimers()
})

describe('Lane F1 pure geometry, hash & telemetry helpers', () => {
  it('parses valid #spot=x,y coordinates and rejects out-of-bounds or malformed hashes', () => {
    expect(parseSpotHash('#spot=25,75')).toEqual([25, 75])
    expect(parseSpotHash('#foo=bar&spot=0,100')).toEqual([0, 100])
    expect(parseSpotHash('spot=42.5,18.25')).toEqual([42.5, 18.25])
    expect(parseSpotHash('')).toBeNull()
    expect(parseSpotHash('#other=1')).toBeNull()
    expect(parseSpotHash('#spot=-1,50')).toBeNull()
    expect(parseSpotHash('#spot=50,100.1')).toBeNull()
    expect(parseSpotHash('#spot=abc,20')).toBeNull()
  })

  it('diffs dropped candidate boxes by exact id or IoU >= 0.5 per floor', () => {
    const c1 = makeWindowBox(1, 10, 10)
    const c2 = makeWindowBox(2, 30, 10) // re-minted id at identical position
    const c3 = makeWindowBox(3, 60, 80) // south window -> dropped
    const cOtherFloor = makeWindowBox(4, 20, 20, { drawing_id: 'floor-og1' })

    const final1 = c1
    const final2Reminted = { ...c2, id: 'win-final-2', x: 30.2, y: 10.1 }
    expect(boxIoU(c2, final2Reminted)).toBeGreaterThanOrEqual(0.5)

    const dropped = diffDroppedBoxes([c1, c2, c3, cOtherFloor], [final1, final2Reminted], bundledDrawing.id)
    expect(dropped.map((b) => b.id)).toEqual(['win-3'])
  })

  it('resolves --group-1..6 tokens and extracts streaming agent telemetry', () => {
    const groups: EvidenceGroupLike[] = [
      { id: 'g-a', label: 'Apt A', color: 'c1' },
      { id: 'g-b', label: 'Apt B', color: 'c2' },
    ]
    expect(resolveGroupStyle('g-a', groups)?.main).toBe('var(--group-1)')
    expect(resolveGroupStyle('g-b', groups)?.surface).toBe('var(--group-2-surface)')
    expect(resolveGroupStyle('missing', groups)).toBeNull()

    const stream: StreamState = {
      phase: 'working',
      nodes: [
        {
          id: 'n1',
          kind: 'model',
          title: 'Inspecting north facade',
          tag: 'deep_agent',
          body: '',
          tool: {
            name: 'deep_agent',
            result_id: 'r1',
            ms: 25,
            args: {
              seq: 3,
              step: 'Checking north orientation',
              focus_rect: { x: 10, y: 5, w: 30, h: 15 },
              dropped_ids: ['win-5', 'win-6'],
              kept_ids: ['win-1'],
            },
          },
        },
      ],
      resolved: ['n1'],
      evidence: {
        boxes: [makeWindowBox(1, 12, 8), makeWindowBox(5, 40, 85), makeWindowBox(6, 70, 85)],
        rooms: [],
      },
    }
    const tel = extractAgentTelemetry(stream, bundledDrawing.id, 'en')
    expect(tel.stepIndex).toBe(3)
    expect(tel.stepLabel).toBe('Checking north orientation')
    expect(tel.markedCount).toBe(3)
    expect(tel.droppedCount).toBe(2)
    expect(tel.focusRect).toEqual({ x: 10, y: 5, w: 30, h: 15 })
  })
})

describe('Live agent visualization & "show all north windows" dropped-candidates guardrail', () => {
  it('shows 18 candidates during pending, settles for 1.8s on done, then highlights ONLY the 4 confirmed north windows unless toggled', () => {
    vi.useFakeTimers()
    const all18 = Array.from({ length: 18 }, (_, idx) =>
      makeWindowBox(idx + 1, 5 + (idx % 6) * 14, idx < 4 ? 8 : 78),
    )
    const north4 = all18.slice(0, 4)
    const dropped14Ids = all18.slice(4).map((b) => b.id)

    const streamState: StreamState = {
      phase: 'working',
      nodes: [
        {
          id: 'node-scan',
          kind: 'model',
          title: 'Inspecting facade crops',
          tag: 'deep_agent',
          body: '',
          tool: {
            name: 'deep_agent',
            result_id: 'r-scan',
            ms: 40,
            args: {
              seq: 2,
              step: 'Filtering north facade',
              focus_rect: { x: 4, y: 6, w: 22, h: 10 },
              dropped_ids: dropped14Ids,
              kept_ids: north4.map((b) => b.id),
            },
          },
        },
      ],
      resolved: ['node-scan'],
      evidence: { boxes: all18, rooms: [] },
    }

    const pendingTurn: AgentTurn = {
      id: 'turn-north',
      role: 'agent',
      replyTo: 'u-1',
      status: 'pending',
      stream: streamState,
      toggles: emptyToggles(),
      listOpen: false,
      showCanonical: false,
      feedback: null,
    }

    appStore.setState((s) => ({
      turns: [pendingTurn],
      ui: { ...s.ui, activeTurnId: 'turn-north' },
    }))

    renderUi(<PlanStage />)

    // 1. During pending: HUD + Reticle + all 18 candidates are visible, with 14 marked dropped.
    const hud = container.querySelector('[data-agent-hud]')
    expect(hud?.textContent).toContain('Step 2 · Filtering north facade')
    expect(hud?.textContent).toContain('18 marked')
    expect(hud?.textContent).toContain('14 dropped')
    expect(container.querySelector('[data-agent-reticle]')).not.toBeNull()
    expect(container.querySelectorAll('button[data-evidence-id]')).toHaveLength(18)
    expect(container.querySelectorAll('button[data-agent-state="dropped"]')).toHaveLength(14)
    expect(container.querySelectorAll('button[data-agent-state="inspecting"]').length).toBeGreaterThan(0)

    // 2. Transition to done with ONLY the 4 north windows in response.evidence.boxes.
    const doneTurn: AgentTurn = {
      ...pendingTurn,
      status: 'done',
      response: makeCountResponse(north4),
    }
    act(() => {
      appStore.setState({ turns: [doneTurn] })
    })

    // Immediately upon done (before 1.8s): interactive evidence buttons are strictly the 4 north windows,
    // while the 14 dropped candidates play their brief 1.8s ghost settle.
    expect(container.querySelector('[data-agent-hud]')).toBeNull()
    expect(container.querySelector('[data-agent-reticle]')).toBeNull()
    expect(container.querySelectorAll('button[data-evidence-id]')).toHaveLength(4)
    expect(container.querySelectorAll('[data-dropped-id]')).toHaveLength(14)

    // 3. After the 1.8s drop-settle completes: ONLY the 4 north windows remain on the blueprint!
    act(() => {
      vi.advanceTimersByTime(1850)
    })
    expect(container.querySelectorAll('button[data-evidence-id]')).toHaveLength(4)
    expect(container.querySelectorAll('[data-dropped-id]')).toHaveLength(0)
    expect(container.querySelectorAll('[data-agent-state="dropped"]')).toHaveLength(0)

    // 4. Toggle chip ("Show 14 filtered out") explicitly reveals and re-hides muted ghosts without adding evidence buttons.
    const toggleBtn = container.querySelector<HTMLButtonElement>('[data-toggle-dropped]')
    expect(toggleBtn).not.toBeNull()
    expect(toggleBtn?.getAttribute('aria-pressed')).toBe('false')
    expect(toggleBtn?.textContent).toContain('Show 14 filtered out')

    act(() => {
      toggleBtn?.click()
    })
    expect(toggleBtn?.getAttribute('aria-pressed')).toBe('true')
    expect(toggleBtn?.textContent).toContain('Hide 14 filtered out')
    expect(container.querySelectorAll('[data-dropped-id]')).toHaveLength(14)
    expect(container.querySelectorAll('button[data-evidence-id]')).toHaveLength(4)

    act(() => {
      toggleBtn?.click()
    })
    expect(toggleBtn?.getAttribute('aria-pressed')).toBe('false')
    expect(container.querySelectorAll('[data-dropped-id]')).toHaveLength(0)
  })

  it('settles dropped candidates in 0ms immediately when prefers-reduced-motion: reduce is active', () => {
    reducedMotionFlag = true
    const all6 = Array.from({ length: 6 }, (_, idx) => makeWindowBox(idx + 1, 10 + idx * 12, 20))
    const north2 = all6.slice(0, 2)

    const pendingTurn: AgentTurn = {
      id: 'turn-rm',
      role: 'agent',
      replyTo: 'u-rm',
      status: 'pending',
      stream: {
        phase: 'working',
        nodes: [],
        resolved: [],
        evidence: { boxes: all6, rooms: [] },
      },
      toggles: emptyToggles(),
      listOpen: false,
      showCanonical: false,
      feedback: null,
    }

    appStore.setState((s) => ({
      turns: [pendingTurn],
      ui: { ...s.ui, activeTurnId: 'turn-rm' },
    }))
    renderUi(<PlanStage />)
    expect(container.querySelectorAll('button[data-evidence-id]')).toHaveLength(6)

    act(() => {
      appStore.setState({
        turns: [{ ...pendingTurn, status: 'done', response: makeCountResponse(north2) }],
      })
    })

    // 0ms under reduced motion: zero dropped ghosts immediately!
    expect(container.querySelectorAll('button[data-evidence-id]')).toHaveLength(2)
    expect(container.querySelectorAll('[data-dropped-id]')).toHaveLength(0)
  })
})

describe('Spotlight /30 mask, halo badges, categorical groups, LinkLayer & FloorSwitcher', () => {
  it('renders /30 spotlight mask cutouts, single-layer sibling dimming, halo badges, and guards non-count agent_estimate', () => {
    const b1 = makeWindowBox(1, 15, 20, { w: 0.4, h: 0.4 }) // tiny box -> floored to >= 10px in mask
    const b2 = makeWindowBox(2, 55, 20)

    const estimateTurn: AgentTurn = {
      id: 'turn-est',
      role: 'agent',
      replyTo: 'u-est',
      status: 'done',
      response: {
        ...makeCountResponse([b1, b2]),
        method: 'agent_estimate',
        answer: { kind: 'note', value: '1.20 m', number: 1.2, unit: 'm' },
      },
      toggles: emptyToggles(),
      listOpen: false,
      showCanonical: false,
      feedback: null,
    }

    appStore.setState((s) => ({
      turns: [estimateTurn],
      ui: { ...s.ui, activeTurnId: 'turn-est' },
    }))

    renderUi(<PlanStage />)

    expect(container.querySelector('[data-layer="spotlight"]')).toBeNull()
    const btn1 = container.querySelector<HTMLButtonElement>('button[data-evidence-id="win-1"]')!
    const badge1 = container.querySelector<HTMLElement>('[data-badge-for="win-1"]')!
    const badge2 = container.querySelector<HTMLElement>('[data-badge-for="win-2"]')!

    expect(badge1.textContent).toBe('1')
    expect(badge1.className).toContain('ring-2')
    expect(badge1.className).toContain('ring-background')

    // First click selects win-1 and mounts the spotlight mask veil.
    act(() => {
      btn1.click()
    })
    const spotlight = container.querySelector('[data-layer="spotlight"]')
    expect(spotlight).not.toBeNull()
    expect(spotlight?.querySelector('rect.fill-primary\\/30')).not.toBeNull()
    const cutRect = spotlight?.querySelector('mask rect[fill="black"]')
    expect(Number(cutRect?.getAttribute('width'))).toBeGreaterThan(b1.w)

    // Focused badge wrapper is elevated to z-[5]; non-focused sibling badge has opacity-35 once (not on parent).
    expect(badge1.parentElement?.className).toContain('z-[5]')
    expect(badge1.className).not.toContain('opacity-35')
    expect(badge2.className).toContain('opacity-35')
    expect(badge2.parentElement?.className).not.toContain('opacity-35')

    // Second click on non-count agent_estimate box does NOT reject it.
    act(() => {
      btn1.click()
    })
    expect(appStore.getState().turns[0]).toMatchObject({ toggles: { rejected: {} } })
  })

  it('color-codes --group-1..6 rooms/boxes, renders LinkLayer arrows, filters per floor, and switches floors via FloorSwitcher', () => {
    const room0 = bundledDrawing.rooms[0]
    const groups: EvidenceGroupLike[] = [
      { id: 'apt-a', label: 'Apartment A', color: 'c1' },
      { id: 'stair-s1', label: 'Stair S1', color: 'c2', target_drawing_id: 'floor-og1' },
    ]
    const boxes: GroupedBox[] = [
      // Item on upper floor comes #1 in global response, so local floor item keeps badge #2!
      makeWindowBox(1, 20, 20, { id: 'stair-og1', drawing_id: 'floor-og1', group_id: 'stair-s1' }),
      makeWindowBox(2, 40, 45, { id: 'stair-eg', drawing_id: bundledDrawing.id, group_id: 'stair-s1' }),
    ]
    const rooms: GroupedRoom[] = [
      {
        room_id: room0.room_id,
        area_m2: room0.area_m2,
        included: true,
        group_id: 'apt-a',
      },
    ]
    const links: EvidenceLinkLike[] = [
      {
        id: 'lnk-stair-1',
        from_pos: [40, 45],
        waypoints: [[45, 35]],
        to_pos: [52, 28],
        group_id: 'stair-s1',
        label: '→ 1. OG',
        target_drawing_id: 'floor-og1',
      },
    ]
    const project: ProjectBundleLike = {
      id: 'proj-house-1',
      title: 'Villa Aurora',
      floors: [
        { drawing_id: bundledDrawing.id, label: 'EG', order: 0 },
        { drawing_id: 'floor-og1', label: '1. OG', order: 1 },
      ],
    }

    const turn: AgentTurn = {
      id: 'turn-groups',
      role: 'agent',
      replyTo: 'u-g',
      status: 'done',
      response: makeCountResponse(boxes, { rooms, groups, links }),
      toggles: emptyToggles(),
      listOpen: false,
      showCanonical: false,
      feedback: null,
    }

    appStore.setState((s) => ({
      ...s,
      activeProject: project,
      turns: [turn],
      ui: { ...s.ui, activeTurnId: 'turn-groups' },
    } as typeof s))

    let currentPath = `/d/${bundledDrawing.id}`
    function PathProbe() {
      currentPath = useLocation().pathname
      return null
    }

    renderUi(
      <MemoryRouter initialEntries={[`/d/${bundledDrawing.id}`]}>
        <PathProbe />
        <PlanStage />
      </MemoryRouter>,
    )

    // Floor filtering keeps only EG box on stage, preserving its global badge number "2".
    const egBoxes = container.querySelectorAll('button[data-evidence-id]')
    expect(egBoxes).toHaveLength(1)
    expect(egBoxes[0].getAttribute('data-evidence-id')).toBe('stair-eg')
    expect(container.querySelector('[data-badge-for="stair-eg"]')?.textContent).toBe('2')
    expect((egBoxes[0] as HTMLElement).style.borderColor).toBe('var(--group-2)')

    // Room polygon uses --group-1 fill/stroke.
    const poly = container.querySelector<SVGPolygonElement>('[data-layer="rooms"] polygon')!
    expect(poly.style.fill).toBe('var(--group-1)')

    // Group legend spotlights Apartment A when clicked, dimming Stair S1 box.
    const aptChip = container.querySelector<HTMLButtonElement>('[data-group-chip="apt-a"]')!
    act(() => {
      aptChip.click()
    })
    expect(aptChip.getAttribute('aria-pressed')).toBe('true')
    expect(container.querySelector('[data-layer="spotlight"]')).not.toBeNull()
    expect(egBoxes[0].className).toContain('opacity-35')

    // FloorSwitcher shows EG (2 hits: 1 box + 1 room) and 1. OG (1 hit), toggles crossPlanScope, and LinkLayer pill jumps floors.
    const switcher = container.querySelector('[data-floor-switcher]')!
    expect(switcher).not.toBeNull()
    const ogFloorBtn = switcher.querySelector<HTMLButtonElement>('[data-floor-id="floor-og1"]')!
    expect(ogFloorBtn.querySelector('[data-floor-hits]')?.textContent).toBe('1')

    const crossToggle = switcher.querySelector<HTMLButtonElement>('[data-cross-plan-toggle]')!
    expect(crossToggle.getAttribute('aria-pressed')).toBe('false')
    act(() => {
      crossToggle.click()
    })
    expect(crossToggle.getAttribute('aria-pressed')).toBe('true')

    const linkPill = container.querySelector<HTMLButtonElement>('[data-link-target="floor-og1"]')!
    expect(linkPill.textContent).toContain('1. OG')
    act(() => {
      linkPill.click()
    })
    expect(currentPath).toBe('/d/floor-og1')
  })
})

describe('PlanStage 450% cursor zoom, #spot=x,y deep-link & PlanToolbar controls', () => {
  it('zooms smoothly with Ctrl+Wheel up to 450%, ignores plain wheel, and activates #spot=x,y ring', async () => {
    window.location.hash = '#spot=30,70'
    renderUi(
      <TooltipProvider>
        <PlanToolbar />
        <PlanStage />
      </TooltipProvider>,
    )

    // #spot=30,70 boosts zoom to >= 190% and mounts the target ring.
    expect(appStore.getState().ui.zoom).toBe(190)
    const spotRing = container.querySelector<HTMLElement>('[data-spot-ring]')
    expect(spotRing).not.toBeNull()
    expect(spotRing?.style.left).toBe('30%')
    expect(spotRing?.style.top).toBe('70%')

    const scroller = container.querySelector<HTMLElement>('[data-plan-scroller]')!

    // Plain wheel without Ctrl/Cmd does not hijack scrolling or change zoom.
    const plainWheel = new WheelEvent('wheel', { deltaY: -120, cancelable: true, bubbles: true })
    act(() => {
      scroller.dispatchEvent(plainWheel)
    })
    expect(plainWheel.defaultPrevented).toBe(false)
    expect(appStore.getState().ui.zoom).toBe(190)

    // Ctrl+Wheel zooms smoothly and clamps strictly at 450%.
    const ctrlWheelIn = new WheelEvent('wheel', {
      deltaY: -600,
      ctrlKey: true,
      clientX: 300,
      clientY: 250,
      cancelable: true,
      bubbles: true,
    })
    act(() => {
      scroller.dispatchEvent(ctrlWheelIn)
    })
    expect(ctrlWheelIn.defaultPrevented).toBe(true)
    expect(appStore.getState().ui.zoom).toBe(450)

    // At 450%, toolbar Zoom in is disabled and clicking {zoom}% resets to 100%.
    const zoomInBtn = container.querySelector<HTMLButtonElement>('button[aria-label="Zoom in"]')!
    expect(zoomInBtn.disabled).toBe(true)
    const resetPctBtn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('450%'),
    )!
    act(() => {
      resetPctBtn.click()
    })
    expect(appStore.getState().ui.zoom).toBe(100)
    const zoomOutBtn = container.querySelector<HTMLButtonElement>('button[aria-label="Zoom out"]')!
    expect(zoomOutBtn.disabled).toBe(true)

    // Toolbar `?` button opens ShortcutHelpDialog, and answererMode === 'agent' hides What-If.
    const shortcutsBtn = container.querySelector<HTMLButtonElement>('[data-shortcuts-trigger]')!
    act(() => {
      shortcutsBtn.click()
    })
    expect(appStore.getState().ui.shortcutsOpen).toBe(true)

    const whatIfBtn = container.querySelector<HTMLButtonElement>('button[aria-label="What-if sizes"]')
    expect(whatIfBtn).not.toBeNull()
    await act(async () => {
      appStore.getState().setTool('whatif')
      await import('./WhatIf')
    })
    expect(appStore.getState().ui.tool).toBe('whatif')
    await act(async () => {
      appStore.getState().setAnswererMode('agent')
    })
    expect(container.querySelector('button[aria-label="What-if sizes"]')).toBeNull()
    expect(appStore.getState().ui.tool).toBe('select')
  })

  it('keeps #spot=x,y target ring visible under reduced motion and past 700ms, and peels on Escape', () => {
    vi.useFakeTimers()
    reducedMotionFlag = true
    window.location.hash = '#spot=42,58'

    renderUi(<PlanStage />)

    const spotRing = container.querySelector<HTMLElement>('[data-spot-ring]')!
    expect(spotRing).not.toBeNull()
    // Root ring itself must NOT carry .plan-pulse (which has base opacity: 0); inner halo carries .plan-pulse.
    expect(spotRing.classList.contains('plan-pulse')).toBe(false)
    expect(spotRing.className).toContain('opacity-90')
    expect(spotRing.querySelector('.plan-pulse')).not.toBeNull()

    act(() => {
      vi.advanceTimersByTime(1500)
    })
    expect(container.querySelector('[data-spot-ring]')).not.toBeNull()

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    })
    expect(container.querySelector('[data-spot-ring]')).toBeNull()
  })

  it('zooms and pans smoothly on two-finger touch pinch gestures clamped to [100, 450]', () => {
    renderUi(<PlanStage />)
    const scroller = container.querySelector<HTMLElement>('[data-plan-scroller]')!

    const mkTouch = (clientX: number, clientY: number) => ({ clientX, clientY }) as Touch
    const startEvt = new Event('touchstart', { bubbles: true, cancelable: true }) as TouchEvent
    Object.defineProperty(startEvt, 'touches', { value: [mkTouch(200, 200), mkTouch(300, 200)] })
    act(() => {
      scroller.dispatchEvent(startEvt)
    })

    // Spread fingers 2.5x apart while panning midpoint right by +40px -> zoom = 250%
    const moveEvt = new Event('touchmove', { bubbles: true, cancelable: true }) as TouchEvent
    Object.defineProperty(moveEvt, 'touches', { value: [mkTouch(165, 220), mkTouch(415, 220)] })
    act(() => {
      scroller.dispatchEvent(moveEvt)
    })
    expect(moveEvt.defaultPrevented).toBe(true)
    expect(appStore.getState().ui.zoom).toBe(250)

    // Extreme spread clamps at 450%
    const maxEvt = new Event('touchmove', { bubbles: true, cancelable: true }) as TouchEvent
    Object.defineProperty(maxEvt, 'touches', { value: [mkTouch(0, 220), mkTouch(900, 220)] })
    act(() => {
      scroller.dispatchEvent(maxEvt)
    })
    expect(appStore.getState().ui.zoom).toBe(450)
  })
})

describe('Reviewer regression coverage: Wave 0.5 sync, crop_px telemetry, abstain guardrail & FloorSwitcher warmDrawing', () => {
  it('syncs group spotlight bidirectionally with Wave 0.5 setFocusedGroup, previews on mouse hover, and clears via Escape', () => {
    const room0 = bundledDrawing.rooms[0]
    const groups: EvidenceGroupLike[] = [
      { id: 'apt-a', label: 'Apartment A', color: 'c1' },
      { id: 'apt-b', label: 'Apartment B', color: 'c2' },
    ]
    const boxes: GroupedBox[] = [
      makeWindowBox(1, 20, 25, { id: 'win-a', group_id: 'apt-a' }),
      makeWindowBox(2, 60, 45, { id: 'win-b', group_id: 'apt-b' }),
    ]
    const rooms: GroupedRoom[] = [
      { room_id: room0.room_id, area_m2: room0.area_m2, included: true, group_id: 'apt-a' },
    ]
    const turn: AgentTurn = {
      id: 'turn-w05-sync',
      role: 'agent',
      replyTo: 'u-sync',
      status: 'done',
      response: makeCountResponse(boxes, { rooms, groups }),
      toggles: emptyToggles(),
      listOpen: false,
      showCanonical: false,
      feedback: null,
    }
    appStore.setState((s) => ({
      ...s,
      turns: [turn],
      ui: { ...s.ui, activeTurnId: 'turn-w05-sync' },
    }))

    renderUi(<PlanStage />)

    const chipA = container.querySelector<HTMLButtonElement>('[data-group-chip="apt-a"]')!
    const chipB = container.querySelector<HTMLButtonElement>('[data-group-chip="apt-b"]')!

    // Fine-pointer hover previews spotlight without pinning store.focusedGroupId
    // (React 19 delegates onPointerEnter/Leave via bubbling pointerover/pointerout)
    act(() => {
      const enter = new MouseEvent('pointerover', { bubbles: true, relatedTarget: document.body })
      Object.defineProperty(enter, 'pointerType', { value: 'mouse' })
      chipA.dispatchEvent(enter)
    })
    expect(container.querySelector('[data-layer="spotlight"]')).not.toBeNull()
    expect(appStore.getState().focusedGroupId).toBeNull()

    act(() => {
      const leave = new MouseEvent('pointerout', { bubbles: true, relatedTarget: document.body })
      Object.defineProperty(leave, 'pointerType', { value: 'mouse' })
      chipA.dispatchEvent(leave)
    })
    expect(container.querySelector('[data-layer="spotlight"]')).toBeNull()

    // Clicking chipA writes to appStore.focusedGroupId & ui.focusedGroupId
    act(() => {
      chipA.click()
    })
    expect(appStore.getState().focusedGroupId).toBe('apt-a')
    expect(appStore.getState().ui.focusedGroupId).toBe('apt-a')

    // Simulating AgentCard calling setFocusedGroup('apt-b'), then clearing via chipB click and Escape
    act(() => {
      appStore.getState().setFocusedGroup('apt-b')
    })
    expect(chipB.getAttribute('aria-pressed')).toBe('true')
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    })
    expect(appStore.getState().focusedGroupId).toBeNull()
    expect(container.querySelector('[data-layer="spotlight"]')).toBeNull()
  })

  it('converts sidecar crop_px + image_px into PctRect and normalizes machine tool slugs in extractAgentTelemetry', () => {
    const stream: StreamState = {
      phase: 'working',
      nodes: [
        {
          id: 'n-crop',
          title: '',
          tag: 'tool',
          body: '',
          kind: 'model',
          tool: {
            name: 'deep_agent',
            args: {
              seq: 2,
              step: 'view_image',
              crop_px: [200, 100, 600, 500],
              image_px: [1000, 1000],
            },
            result_id: 'r-crop',
            ms: 15,
          },
        },
      ],
      resolved: [],
      evidence: { boxes: [], rooms: [] },
    }

    const enTel = extractAgentTelemetry(stream, bundledDrawing.id, 'en')
    expect(enTel.focusRect).toEqual({ x: 20, y: 10, w: 40, h: 40 })
    expect(enTel.stepIndex).toBe(2)
    expect(enTel.stepLabel).toBe('Inspecting crop')

    const deTel = extractAgentTelemetry(stream, bundledDrawing.id, 'de')
    expect(deTel.stepLabel).toBe('Ausschnitt wird geprüft')
  })

  it('suppresses dropped candidate ghosts and filtered-out toggle when final answer abstains', () => {
    const candidates = [makeWindowBox(1, 15, 20), makeWindowBox(2, 45, 20), makeWindowBox(3, 75, 20)]
    const abstainResp: AskResponse = {
      ...makeCountResponse([]),
      answer: { kind: 'abstain', reason: 'low_confidence' },
      evidence: { boxes: [], rooms: [] },
    }
    const turn: AgentTurn = {
      id: 'turn-abstain',
      role: 'agent',
      replyTo: 'u-abs',
      status: 'done',
      stream: {
        phase: 'checking',
        nodes: [],
        resolved: [],
        evidence: { boxes: candidates, rooms: [] },
      },
      response: abstainResp,
      toggles: emptyToggles(),
      listOpen: false,
      showCanonical: false,
      feedback: null,
    }
    appStore.setState((s) => ({
      ...s,
      turns: [turn],
      ui: { ...s.ui, activeTurnId: 'turn-abstain' },
    }))

    renderUi(<PlanStage />)

    expect(container.querySelectorAll('[data-dropped-id]')).toHaveLength(0)
    expect(container.querySelector('[data-toggle-dropped]')).toBeNull()
  })

  it('pre-warms sibling floors via appStore.warmDrawing even on same-origin empty VITE_API_URL', async () => {
    vi.useFakeTimers()
    const siblingDrawing = { ...bundledDrawing, id: 'floor-og1', images: { rebuilt: '/api/drawings/floor-og1/rebuilt.png' } }
    const warmSpy = vi.spyOn(appStore.getState(), 'warmDrawing').mockResolvedValue(siblingDrawing)

    const project: ProjectBundleLike = {
      id: 'proj-warm',
      title: 'Villa Warm',
      floors: [
        { drawing_id: bundledDrawing.id, label: 'EG', order: 0 },
        { drawing_id: 'floor-og1', label: '1. OG', order: 1 },
      ],
    }
    appStore.setState((s) => ({
      ...s,
      activeProject: project,
      session: { ...s.session, source: { kind: 'api', state: 'ready' } },
    }))

    renderUi(<PlanStage />)

    await act(async () => {
      vi.advanceTimersByTime(60)
      await Promise.resolve()
    })

    expect(warmSpy).toHaveBeenCalledWith('floor-og1')
    expect(document.head.querySelector('link[data-aura-prefetch="floor-og1"]')).not.toBeNull()
    warmSpy.mockRestore()
  })
})

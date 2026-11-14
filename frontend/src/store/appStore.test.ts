import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it } from 'vitest'
import { createSource } from '@/api/source'
import { Command, CommandDialog, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import { Kbd, KbdGroup } from '@/components/ui/kbd'
import { bundledDrawing } from '@/data/drawing'
import { limitsOf, markLabel, nextLabel, overlayOpen, shouldIgnoreShortcut } from '@/features/plan/shared'
import { deriveAnswer } from '@/lib/answer'
import { ZOOM_STEPS, createAppStore, reduceStream, type AgentTurn, type AppStore, type StreamState } from './appStore'

const make = () => createAppStore({ source: createSource({ baseUrl: null }) })
const agentTurn = (store: AppStore, id: string) => store.getState().turns.find((t) => t.id === id) as AgentTurn

describe('store', () => {
  it('ask pushes a user turn and resolves the agent turn', async () => {
    const store = make()
    const p = store.getState().ask('How many windows are drawn on this floor plan?')
    const pending = store.getState().turns
    expect(pending.map((t) => t.role)).toEqual(['user', 'agent'])
    expect((pending[1] as AgentTurn).status).toBe('pending')
    const id = await p
    const turn = agentTurn(store, id)
    expect(turn.status).toBe('done')
    expect(turn.response?.answer).toMatchObject({ kind: 'count', value: 12 })
    expect(store.getState().ui.activeTurnId).toBe(id)
    expect(store.getState().session.source).toMatchObject({ kind: 'local', state: 'ready' })
  })

  it('session lang follows the last question', async () => {
    const store = make()
    await store.getState().ask('Montako ovea tässä pohjapiirroksessa on?')
    expect(store.getState().session.lang).toBe('fi')
  })

  it('tap: first selects, second toggles; undo restores; turn ids are kept', async () => {
    const store = make()
    const id = await store.getState().ask('How many windows are drawn on this floor plan?')
    const s = store.getState
    expect(s().tap(id, 'win-03')).toBe('selected')
    expect(s().ui.selectedEvidenceId).toBe('win-03')
    expect(agentTurn(store, id).toggles.rejected).toEqual({})
    expect(s().tap(id, 'win-03')).toBe('toggled')
    expect(deriveAnswer(agentTurn(store, id).response!, agentTurn(store, id).toggles).value).toBe(11)
    expect(s().undoStack).toEqual([{ turn_id: id, evidence_id: 'win-03', prev: undefined }])
    expect(s().undo()).toMatchObject({ turn_id: id, evidence_id: 'win-03' })
    expect(agentTurn(store, id).toggles.rejected).toEqual({})
    expect(s().undo()).toBeNull()
  })

  it('toggles are per turn; undo goes back to the right turn', async () => {
    const store = make()
    const s = store.getState
    const a = await s().ask('How many windows are drawn on this floor plan?')
    const b = await s().ask('What is the net floor area of apartment A?')
    s().toggle(a, 'win-01')
    s().toggle(b, 'room-3.01')
    expect(deriveAnswer(agentTurn(store, b).response!, agentTurn(store, b).toggles).value).toBe(169.73)
    s().undo()
    expect(agentTurn(store, b).toggles.included).toEqual({})
    expect(agentTurn(store, a).toggles.rejected).toEqual({ 'win-01': true })
    s().resetTurn(a)
    expect(agentTurn(store, a).toggles.rejected).toEqual({})
    expect(s().undoStack).toEqual([])
  })

  it('toggling a room twice returns to the default (no lingering override)', async () => {
    const store = make()
    const id = await store.getState().ask('What is the net floor area of apartment A?')
    store.getState().toggle(id, 'room-3.04')
    expect(agentTurn(store, id).toggles.included).toEqual({ 'room-3.04': false })
    store.getState().toggle(id, 'room-3.04')
    expect(agentTurn(store, id).toggles.included).toEqual({})
  })

  it('unknown evidence ids are ignored', async () => {
    const store = make()
    const id = await store.getState().ask('How many windows are drawn on this floor plan?')
    store.getState().toggle(id, 'door-01')
    expect(store.getState().undoStack).toEqual([])
  })

  it('an attached mark becomes the region scope and is detached after asking', async () => {
    const store = make()
    const s = store.getState
    const markId = s().addMark({ x: 61, y: 54.4, w: -13.6, h: -15.2 })
    expect(s().ui.marks[0].label).toBe('Area 1')
    expect(s().ui.marks[0].rect.x).toBeCloseTo(47.4)
    expect(s().ui.marks[0].rect.w).toBeCloseTo(13.6)
    const id = await s().ask('Combien de lavabos ici ?')
    expect(agentTurn(store, id).response?.answer).toMatchObject({ kind: 'count', value: 3 })
    expect(agentTurn(store, id).response?.scope).toMatchObject({ kind: 'region', label: 'Area 1' })
    expect(s().turns[0]).toMatchObject({ role: 'user', markId })
    expect(s().ui.attachedMarkId).toBeNull()
  })

  it('what-if: start, draft, apply → a new area turn with the override', async () => {
    const store = make()
    const s = store.getState
    s().startWhatIf('3.04')
    expect(s().ui).toMatchObject({ tool: 'whatif', whatIf: { roomId: '3.04', w: 4.135, h: 4.12 } })
    s().setWhatIfDraft(4.135, 3.6)
    const id = await s().applyWhatIf()
    expect(id).not.toBeNull()
    expect(agentTurn(store, id!).response?.answer).toMatchObject({ kind: 'area', value: 137.19 })
    expect(s().ui.tool).toBe('select')
  })

  it('zoom steps and continuous zoom are clamped; compare opacity clamped', () => {
    const store = make()
    const s = store.getState
    s().zoomOut()
    expect(s().ui.zoomIndex).toBe(0)
    expect(s().ui.zoom).toBe(100)
    for (let i = 0; i < 8; i++) s().zoomIn()
    expect(s().ui.zoomIndex).toBe(ZOOM_STEPS.length - 1)
    expect(s().ui.zoom).toBe(450)
    s().setZoom(215.4)
    expect(s().ui.zoom).toBe(215)
    s().zoomIn()
    expect(s().ui.zoom).toBe(260)
    s().setZoom(999)
    expect(s().ui.zoom).toBe(450)
    s().setZoom(20)
    expect(s().ui.zoom).toBe(100)
    s().setZoom(350)
    s().resetZoom()
    expect(s().ui.zoom).toBe(100)
    expect(s().ui.zoomIndex).toBe(0)
    s().setCompare({ on: true, opacity: 140 })
    expect(s().ui.compare).toEqual({ on: true, mode: 'swipe', opacity: 100 })
  })

  it('stepEvidence cycles forward and backward across the active turn evidence', async () => {
    const store = make()
    const s = store.getState
    expect(s().stepEvidence(1)).toBeNull()
    const id = await s().ask('How many windows are drawn on this floor plan?')
    const boxes = agentTurn(store, id).response!.evidence.boxes
    expect(s().stepEvidence(1)).toBe(boxes[0].id)
    expect(s().ui.selectedEvidenceId).toBe(boxes[0].id)
    expect(s().stepEvidence(1)).toBe(boxes[1].id)
    expect(s().stepEvidence(-1)).toBe(boxes[0].id)
    expect(s().stepEvidence(-1)).toBe(boxes[boxes.length - 1].id)
  })

  it('shortcutsOpen and commandOpen are mutually exclusive; answererMode syncs cleanly', () => {
    const store = make()
    const s = store.getState
    expect(s().answererMode).toBe('hybrid')
    s().setAnswererMode('agent')
    expect(s().answererMode).toBe('agent')
    expect(s().session.answererMode).toBe('agent')
    s().setShortcutsOpen(true)
    expect(s().ui.shortcutsOpen).toBe(true)
    s().setCommandOpen(true)
    expect(s().ui.commandOpen).toBe(true)
    expect(s().ui.shortcutsOpen).toBe(false)
  })

  it('reduceStream tracks progress queue_position and clears it once work begins', () => {
    const init: StreamState = { phase: 'sent', nodes: [], resolved: [], evidence: { boxes: [], rooms: [] } }
    const queued = reduceStream(init, { event: 'progress', data: { queue_position: 2 } })
    expect(queued.queuePosition).toBe(2)
    const active = reduceStream(queued, {
      event: 'understood',
      data: { canonical_en: 'How many windows?', detected_lang: 'en', route: 'agent' },
    })
    expect(active.queuePosition).toBeUndefined()
    expect(active.phase).toBe('understood')
  })

  it('guards toggle and tap on non-count agent_estimate turns while allowing count box toggles', async () => {
    const store = make()
    const id = await store.getState().ask('How many windows are drawn on this floor plan?')
    const baseResp = agentTurn(store, id).response!
    const boxId = baseResp.evidence.boxes[0].id

    // Mutate turn response to simulate a 1.2 agent_estimate note turn with a box
    store.setState((st) => ({
      turns: st.turns.map((x) =>
        x.id === id && x.role === 'agent'
          ? {
              ...x,
              response: {
                ...baseResp,
                method: 'agent_estimate',
                answer: { kind: 'note', value: 'Approx 4.2 m corridor' },
              },
            }
          : x,
      ),
    }))
    expect(store.getState().tap(id, boxId)).toBe('selected')
    expect(store.getState().tap(id, boxId)).toBe('selected')
    store.getState().toggle(id, boxId)
    expect(agentTurn(store, id).toggles.rejected).toEqual({})

    // Count agent_estimate boxes remain rejectable
    store.setState((st) => ({
      turns: st.turns.map((x) =>
        x.id === id && x.role === 'agent'
          ? {
              ...x,
              response: {
                ...baseResp,
                method: 'agent_estimate',
                answer: { kind: 'count', value: 12, subject: 'window' },
              },
            }
          : x,
      ),
    }))
    expect(store.getState().tap(id, boxId)).toBe('toggled')
    expect(agentTurn(store, id).toggles.rejected[boxId]).toBe(true)
  })

  it('newConversation returns a restore callback that undoes clearing before new turns arrive', async () => {
    const store = make()
    await store.getState().ask('How many windows are drawn on this floor plan?')
    expect(store.getState().turns).toHaveLength(2)
    const restore = store.getState().newConversation()
    expect(store.getState().turns).toHaveLength(0)
    restore()
    expect(store.getState().turns).toHaveLength(2)
  })

  it('a failing source puts the turn in error state', async () => {
    const store = createAppStore({
      source: {
        ...createSource({ baseUrl: null }),
        ask: () => Promise.reject(new Error('boom')),
      },
    })
    const id = await store.getState().ask('How many windows?')
    expect(agentTurn(store, id)).toMatchObject({ status: 'error', error: { message: 'boom' } })
  })
})

describe('plan shared helpers & shortcut guards', () => {
  it('computes nextLabel, markLabel and limitsOf accurately', () => {
    expect(nextLabel('Area', [{ label: 'Area 1' }, { label: 'Spot 1' }])).toBe('Area 2')
    expect(nextLabel('Spot', [{ label: 'Area 1' }, { label: 'Spot 1' }])).toBe('Spot 2')
    expect(markLabel('Custom note')).toBe('Custom note')
    const room = bundledDrawing.rooms.find((r) => r.room_id === '3.04')!
    const lim = limitsOf(room)
    expect(lim.maxW).toBeGreaterThan(room.rect!.w_m)
    expect(lim.maxH).toBeGreaterThan(room.rect!.h_m)
  })

  it('shouldIgnoreShortcut guards IME, inputs, sliders and non-tooltip poppers while allowing tooltips', () => {
    const div = document.createElement('div')
    document.body.appendChild(div)
    expect(shouldIgnoreShortcut({ target: div })).toBe(false)
    expect(shouldIgnoreShortcut({ target: div, defaultPrevented: true })).toBe(true)
    expect(shouldIgnoreShortcut({ target: div, isComposing: true })).toBe(true)
    expect(shouldIgnoreShortcut({ target: div, keyCode: 229 })).toBe(true)

    const input = document.createElement('input')
    expect(shouldIgnoreShortcut({ target: input })).toBe(true)

    const slider = document.createElement('div')
    slider.setAttribute('role', 'slider')
    const thumb = document.createElement('span')
    slider.appendChild(thumb)
    document.body.appendChild(slider)
    expect(shouldIgnoreShortcut({ target: thumb })).toBe(true)
    slider.remove()

    // Tooltip wrapper must NOT block shortcuts
    const tipWrap = document.createElement('div')
    tipWrap.setAttribute('data-radix-popper-content-wrapper', '')
    const tip = document.createElement('div')
    tip.setAttribute('data-slot', 'tooltip-content')
    tipWrap.appendChild(tip)
    document.body.appendChild(tipWrap)
    expect(overlayOpen()).toBe(false)
    expect(shouldIgnoreShortcut({ target: div })).toBe(false)
    tipWrap.remove()

    // Non-tooltip popover wrapper MUST block shortcuts while open, but NOT once data-state="closed"
    const popWrap = document.createElement('div')
    popWrap.setAttribute('data-radix-popper-content-wrapper', '')
    const pop = document.createElement('div')
    pop.setAttribute('data-slot', 'popover-content')
    popWrap.appendChild(pop)
    document.body.appendChild(popWrap)
    expect(overlayOpen()).toBe(true)
    expect(shouldIgnoreShortcut({ target: div })).toBe(true)
    pop.setAttribute('data-state', 'closed')
    expect(overlayOpen()).toBe(false)
    expect(shouldIgnoreShortcut({ target: div })).toBe(false)
    popWrap.remove()

    // Closing dialog (data-state="closed") must NOT block shortcuts; open dialog MUST block
    const dlg = document.createElement('div')
    dlg.setAttribute('role', 'dialog')
    dlg.setAttribute('data-state', 'closed')
    document.body.appendChild(dlg)
    expect(overlayOpen()).toBe(false)
    dlg.setAttribute('data-state', 'open')
    expect(overlayOpen()).toBe(true)
    dlg.remove()

    // Descendant inside contenteditable="true" and ARIA combobox MUST block shortcuts
    const ce = document.createElement('div')
    ce.setAttribute('contenteditable', 'true')
    const ceSpan = document.createElement('span')
    ce.appendChild(ceSpan)
    document.body.appendChild(ce)
    expect(shouldIgnoreShortcut({ target: ceSpan })).toBe(true)
    ce.remove()

    const combo = document.createElement('div')
    combo.setAttribute('role', 'combobox')
    document.body.appendChild(combo)
    expect(shouldIgnoreShortcut({ target: combo })).toBe(true)
    combo.remove()

    div.remove()
  })

  it('CommandDialog portals title inside dialog-content and enforces 40px targets', () => {
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    if (!globalThis.ResizeObserver) {
      globalThis.ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
      } as unknown as typeof ResizeObserver
    }
    if (!Element.prototype.scrollIntoView) {
      Element.prototype.scrollIntoView = () => {}
    }

    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)

    const inner = [
      createElement(CommandInput, { key: 'in', placeholder: 'Search…' }),
      createElement(
        CommandList,
        { key: 'list' },
        createElement(
          CommandGroup,
          { heading: 'Actions' },
          createElement(
            CommandItem,
            { value: 'fit' },
            'Fit drawing',
            createElement(KbdGroup, null, createElement(Kbd, null, '0')),
          ),
        ),
      ),
    ]

    const tree = (open: boolean, wrapCommand = false) =>
      createElement(
        CommandDialog,
        { open, title: 'Command switcher', description: 'Jump to action', showCloseButton: true },
        wrapCommand ? createElement(Command, null, ...inner) : inner,
      )

    act(() => {
      root.render(tree(false))
    })
    expect(document.querySelector('[data-slot="dialog-title"]')).toBeNull()

    for (const wrap of [false, true]) {
      act(() => {
        root.render(tree(true, wrap))
      })
      const content = document.querySelector('[data-slot="dialog-content"]')
      expect(content).not.toBeNull()
      expect(content!.querySelectorAll('[data-slot="command"]')).toHaveLength(1)
      expect(content!.querySelector('[data-slot="dialog-title"]')?.textContent).toBe('Command switcher')
      expect(content!.querySelector('[data-slot="dialog-close"]')?.getAttribute('data-size')).toBe('icon')
      expect(content!.querySelector('[data-slot="input-group"]')?.className).toContain('h-10!')
      expect(content!.querySelector('[data-slot="command-item"]')?.className).toContain('min-h-10')
      expect(content!.querySelector('[data-slot="kbd"]')?.textContent).toBe('0')
    }

    act(() => {
      root.unmount()
    })
    host.remove()
  })
})

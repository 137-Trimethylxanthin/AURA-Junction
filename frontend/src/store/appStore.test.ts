import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it } from 'vitest'
import type { AskRequest, Drawing, ProjectBundle } from '@/api/contract'
import { createSource, type Source } from '@/api/source'
import { Command, CommandDialog, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import { Kbd, KbdGroup } from '@/components/ui/kbd'
import { bundledDrawing } from '@/data/drawing'
import { limitsOf, markLabel, nextLabel, overlayOpen, shouldIgnoreShortcut } from '@/features/plan/shared'
import { deriveAnswer } from '@/lib/answer'
import {
  CONVERSATION_KEY,
  ZOOM_STEPS,
  createAppStore,
  reduceStream,
  type AgentTurn,
  type AppStore,
  type StreamState,
} from './appStore'

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

  it('reduceStream merges streamed groups and links alongside boxes and rooms', () => {
    const init: StreamState = { phase: 'working', nodes: [], resolved: [], evidence: { boxes: [], rooms: [] } }
    const step1 = reduceStream(init, {
      event: 'tool_result',
      data: {
        node_id: 'n1',
        summary: 'Apartments A and B',
        evidence: {
          boxes: [{ id: 'st-1', subject: 'door', x: 10, y: 10, w: 5, h: 5, group_id: 'stair-1', drawing_id: 'swiss-46639' }],
          rooms: [{ room_id: '3.04', included: true, area_m2: 14.8, group_id: 'unit-A' }],
          groups: [{ id: 'unit-A', label: 'Apartment A', color: 'c1' }],
          links: [{ id: 'lnk-1', from_pos: [12, 12], to_pos: [40, 20], group_id: 'stair-1', label: 'Leads to 1. OG', target_drawing_id: 'fl-1' }],
        },
      },
    })
    const step2 = reduceStream(step1, {
      event: 'tool_result',
      data: {
        node_id: 'n2',
        summary: 'Apartment B',
        evidence: {
          boxes: [{ id: 'st-1', subject: 'door', x: 10, y: 10, w: 5, h: 5 }],
          rooms: [{ room_id: '3.08', included: true, area_m2: 12.1, group_id: 'unit-B' }],
          groups: [
            { id: 'unit-A', label: 'Apartment A', color: 'c1' },
            { id: 'unit-B', label: 'Apartment B', color: 'c2' },
          ],
          links: [{ id: 'lnk-2', from_pos: [50, 50], to_pos: [60, 60], group_id: 'unit-B' }],
        },
      },
    })
    expect(step2.evidence.boxes).toHaveLength(1)
    expect(step2.evidence.rooms.map((r) => r.room_id)).toEqual(['3.04', '3.08'])
    expect(step2.evidence.groups?.map((g) => [g.id, g.color])).toEqual([
      ['unit-A', 'c1'],
      ['unit-B', 'c2'],
    ])
    expect(step2.evidence.links?.map((l) => l.id)).toEqual(['lnk-1', 'lnk-2'])
  })

  it('supports activeProject floor stepping, focusedGroupId, crossPlanScope and shared conversation across floors', async () => {
    const floorEg: Drawing = { ...bundledDrawing, id: 'house-eg', title: 'Haus · EG' }
    const floorOg: Drawing = { ...bundledDrawing, id: 'house-og', title: 'Haus · 1. OG' }
    const floorKg: Drawing = { ...bundledDrawing, id: 'house-kg', title: 'Haus · KG' }
    const unrelated: Drawing = { ...bundledDrawing, id: 'other-plan', title: 'Other' }
    const map: Record<string, Drawing> = {
      'swiss-46639': bundledDrawing,
      'house-kg': floorKg,
      'house-eg': floorEg,
      'house-og': floorOg,
      'other-plan': unrelated,
    }
    const seenReqs: AskRequest[] = []
    const base = createSource({ baseUrl: null })
    const baseResp = (await base.ask({ drawing_id: 'swiss-46639', question: 'How many apartments are there?' })).response
    const src: Source = {
      ...base,
      hasApi: () => true,
      status: () => ({ kind: 'api', state: 'ready' }),
      drawing: async (id) => map[id],
      async chat(req, onEvent) {
        seenReqs.push(req)
        onEvent({ event: 'turn', data: { conversation_id: 'conv-house-1', turn_id: 't-1' } })
        return { response: baseResp, fellBack: false }
      },
    }

    const bundle: ProjectBundle = {
      id: 'proj-house-1',
      title: 'Haus Sonnenberg',
      floors: [
        { drawing_id: 'house-og', label: '1. OG', order: 1 },
        { drawing_id: 'house-kg', label: 'KG', order: -1 },
        { drawing_id: 'house-eg', label: 'EG', order: 0 },
      ],
    }

    localStorage.removeItem(CONVERSATION_KEY('house-eg', 'proj-house-1'))
    const store = createAppStore({ source: src, drawing: floorEg })
    const s = store.getState
    s().setActiveProject(bundle)

    // Floors sorted [-1, 0, 1]: stepping from EG (0) goes up to 1. OG and down to KG, clamping at ends
    expect(s().activeProject?.floors.map((f) => f.drawing_id)).toEqual(['house-kg', 'house-eg', 'house-og'])
    expect(s().stepFloor(1)).toBe('house-og')
    expect(s().stepFloor(-1)).toBe('house-kg')

    // Pre-warm 1. OG and ask a cross-plan question on EG
    await s().warmDrawing('house-og')
    expect(s().drawingById['house-og']?.title).toBe('Haus · 1. OG')
    s().setCrossPlanScope(true)
    const turnId = await s().ask('Match stairs across all floors')
    expect(seenReqs[0].scope).toEqual({
      kind: 'project',
      project_id: 'proj-house-1',
      drawing_ids: ['house-kg', 'house-eg', 'house-og'],
    })
    expect(localStorage.getItem(CONVERSATION_KEY('house-eg', 'proj-house-1'))).toBe('conv-house-1')

    s().setFocusedGroup('unit-A')
    expect(s().focusedGroupId).toBe('unit-A')
    expect(s().ui.focusedGroupId).toBe('unit-A')

    // Switching to sibling floor `house-og` preserves turns, conversationId, activeTurnId and focusedGroupId!
    await s().loadDrawing('house-og')
    expect(s().session.drawing.id).toBe('house-og')
    expect(s().turns).toHaveLength(2)
    expect(s().session.conversationId).toBe('conv-house-1')
    expect(s().ui.activeTurnId).toBe(turnId)
    expect(s().focusedGroupId).toBe('unit-A')
    // Clamped at top floor (`1. OG`)
    expect(s().stepFloor(1)).toBeNull()
    expect(s().stepFloor(-1)).toBe('house-eg')

    // Switching to an unrelated drawing resets turns, conversationId, activeProject and focusedGroupId
    await s().loadDrawing('other-plan')
    expect(s().turns).toHaveLength(0)
    expect(s().session.conversationId).toBeNull()
    expect(s().activeProject).toBeNull()
    expect(s().crossPlanScope).toBe(false)
    expect(s().focusedGroupId).toBeNull()
  })

  it('keeps an active SSE stream running across mid-stream floor switches and resets compare when target lacks original', async () => {
    const floorEg: Drawing = {
      ...bundledDrawing,
      id: 'stream-eg',
      title: 'Haus · EG',
      images: { rebuilt: '/eg.svg', original: '/eg-orig.png' },
    }
    const floorOg: Drawing = {
      ...bundledDrawing,
      id: 'stream-og',
      title: 'Haus · 1. OG',
      images: { rebuilt: '/og.svg' },
    }
    const base = createSource({ baseUrl: null })
    const baseResp = (await base.ask({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })).response
    let finishStream!: () => void
    const src: Source = {
      ...base,
      hasApi: () => true,
      status: () => ({ kind: 'api', state: 'ready' }),
      drawing: async (id) => (id === 'stream-og' ? floorOg : floorEg),
      async chat(_req, onEvent, signal) {
        onEvent({ event: 'turn', data: { conversation_id: 'conv-stream-1', turn_id: 't-1' } })
        onEvent({
          event: 'tool_result',
          data: {
            node_id: 'n1',
            summary: 'Stairs on EG',
            evidence: {
              boxes: [
                { id: 'box-eg', subject: 'door', x: 10, y: 10, w: 5, h: 5, drawing_id: 'stream-eg', group_id: 'g-1' },
                { id: 'box-og', subject: 'door', x: 20, y: 20, w: 5, h: 5, drawing_id: 'stream-og', group_id: 'g-1' },
              ],
              rooms: [],
              groups: [{ id: 'g-1', label: 'Stair S1', color: 'c1', target_drawing_id: 'stream-og' }],
            },
          },
        })
        await new Promise<void>((r) => {
          finishStream = r
        })
        if (signal?.aborted) throw new DOMException('Stopped', 'AbortError')
        const finalResp = {
          ...baseResp,
          evidence: {
            boxes: [
              { id: 'box-eg', subject: 'door' as const, x: 10, y: 10, w: 5, h: 5, drawing_id: 'stream-eg', group_id: 'g-1' },
              { id: 'box-og', subject: 'door' as const, x: 20, y: 20, w: 5, h: 5, drawing_id: 'stream-og', group_id: 'g-1' },
            ],
            rooms: [],
            groups: [{ id: 'g-1', label: 'Stair S1', color: 'c1' as const, target_drawing_id: 'stream-og' }],
          },
        }
        onEvent({ event: 'answer', data: { response: finalResp } })
        return { response: finalResp, fellBack: false }
      },
    }

    const bundle: ProjectBundle = {
      id: 'proj-stream',
      title: 'Stream House',
      floors: [
        { drawing_id: 'stream-eg', label: 'EG', order: 0 },
        { drawing_id: 'stream-og', label: '1. OG', order: 1 },
      ],
    }
    const store = createAppStore({ source: src, drawing: floorEg })
    const s = store.getState
    s().setActiveProject(bundle)
    s().setCompare({ on: true })
    expect(s().ui.compare.on).toBe(true)

    const askPromise = s().ask('Show all stairs and where they lead to')
    // Switch to 1. OG mid-stream BEFORE `finishStream()` resolves
    await s().loadDrawing('stream-og')
    expect(s().session.drawing.id).toBe('stream-og')
    // Compare resets cleanly because `stream-og` has no `images.original`
    expect(s().ui.compare.on).toBe(false)

    finishStream()
    const turnId = await askPromise
    expect(agentTurn(store, turnId).status).toBe('done')
    expect(s().session.conversationId).toBe('conv-stream-1')

    // stepEvidence on `stream-og` cycles only `box-og` (current floor) instead of jumping to `box-eg`
    expect(s().stepEvidence(1)).toBe('box-og')
    expect(s().stepEvidence(1)).toBe('box-og')
  })

  it('restores project conversation on async setActiveProject hydration, clears all floor keys on newConversation, and cancels slow non-SSE turns', async () => {
    const floorEg: Drawing = { ...bundledDrawing, id: 'hyd-eg', title: 'Haus · EG' }
    const floorOg: Drawing = { ...bundledDrawing, id: 'hyd-og', title: 'Haus · 1. OG' }
    const base = createSource({ baseUrl: null })
    const baseResp = (await base.ask({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })).response
    let resolveSlowChat!: () => void
    const src: Source = {
      ...base,
      hasApi: () => true,
      status: () => ({ kind: 'api', state: 'ready' }),
      drawing: async (id) => (id === 'hyd-og' ? floorOg : floorEg),
      conversation: async (id) =>
        id === 'conv-proj-saved'
          ? {
              contract_version: '1.2',
              id: 'conv-proj-saved',
              drawing_id: 'hyd-eg',
              turns: [{ turn_id: 't-saved-1', message: 'Saved project turn', response: baseResp }],
            }
          : null,
      async chat() {
        await new Promise<void>((r) => {
          resolveSlowChat = r
        })
        return { response: baseResp, fellBack: false }
      },
    }

    const bundle: ProjectBundle = {
      id: 'proj-hyd',
      title: 'Hydration House',
      floors: [
        { drawing_id: 'hyd-eg', label: 'EG', order: 0 },
        { drawing_id: 'hyd-og', label: '1. OG', order: 1 },
      ],
    }

    // Seed both a project conversation and an older single-floor fallback key on `hyd-eg`
    localStorage.setItem(CONVERSATION_KEY('hyd-eg', 'proj-hyd'), 'conv-proj-saved')
    localStorage.setItem(CONVERSATION_KEY('hyd-eg'), 'conv-stale-eg')
    localStorage.removeItem(CONVERSATION_KEY('hyd-og'))

    // Open sibling `hyd-og`: initial restoreConversation runs before setActiveProject hydrates
    const store = createAppStore({ source: src, drawing: floorOg })
    const s = store.getState
    await s().restoreConversation()
    expect(s().turns).toHaveLength(0)

    // Async project hydration arrives and restores `conv-proj-saved`
    s().setActiveProject(bundle)
    await new Promise((r) => setTimeout(r, 0))
    expect(s().session.conversationId).toBe('conv-proj-saved')
    expect(s().turns).toHaveLength(2)

    // Clearing conversation on `hyd-og` also removes `hyd-eg`'s stale single-floor key
    s().newConversation()
    expect(localStorage.getItem(CONVERSATION_KEY('hyd-og', 'proj-hyd'))).toBeNull()
    expect(localStorage.getItem(CONVERSATION_KEY('hyd-eg'))).toBeNull()

    // Starting a slow non-SSE fallback turn and clicking Stop (`cancel()`) keeps it `'cancelled'` even after resolution
    const slowPromise = s().ask('Slow fallback question')
    s().cancel()
    resolveSlowChat()
    const slowId = await slowPromise
    expect(agentTurn(store, slowId)).toMatchObject({ status: 'error', error: { code: 'cancelled' } })
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

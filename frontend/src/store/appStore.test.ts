import { describe, expect, it } from 'vitest'
import { createSource } from '@/api/source'
import { deriveAnswer } from '@/lib/answer'
import { createAppStore, type AgentTurn, type AppStore } from './appStore'

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

  it('zoom steps are clamped; compare opacity clamped', () => {
    const store = make()
    const s = store.getState
    s().zoomOut()
    expect(s().ui.zoomIndex).toBe(0)
    for (let i = 0; i < 6; i++) s().zoomIn()
    expect(s().ui.zoomIndex).toBe(3)
    s().setCompare({ on: true, opacity: 140 })
    expect(s().ui.compare).toEqual({ on: true, mode: 'swipe', opacity: 100 })
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

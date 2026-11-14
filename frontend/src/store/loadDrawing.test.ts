import { describe, expect, it } from 'vitest'
import { ApiError, TransportError } from '@/api/client'
import type { Drawing, DrawingStatusBody } from '@/api/contract'
import { createSource, type Source } from '@/api/source'
import { bundledDrawing } from '@/data/drawing'
import { createAppStore } from './appStore'

/** A source with the local answers and a scripted drawing API. */
function fakeSource(api: { drawing?: (id: string) => Promise<Drawing>; status?: (id: string) => Promise<DrawingStatusBody> }): Source {
  const base = createSource({ baseUrl: null })
  return {
    ...base,
    hasApi: () => true,
    drawing: api.drawing ?? (() => Promise.reject(new TransportError('down', 'network'))),
    drawingStatus: api.status ?? (() => Promise.reject(new TransportError('down', 'network'))),
  }
}
const other: Drawing = { ...bundledDrawing, id: 'up-1', title: 'Upload' }
const processing = () => new ApiError('Still processing', 409, 'processing')

describe('loadDrawing', () => {
  it('the bundled drawing is ready at once, without the API', async () => {
    const store = createAppStore({ source: createSource({ baseUrl: null }) })
    await store.getState().loadDrawing('swiss-46639')
    expect(store.getState().session.load).toEqual({ id: 'swiss-46639', state: 'ready' })
  })

  it('without an API another id is unavailable and the bundled drawing stays', async () => {
    const store = createAppStore({ source: createSource({ baseUrl: null }) })
    await store.getState().loadDrawing('up-1')
    expect(store.getState().session.load).toMatchObject({ id: 'up-1', state: 'unavailable' })
    expect(store.getState().session.drawing.id).toBe('swiss-46639')
  })

  it('polls while processing, then swaps the drawing in and starts a clean conversation', async () => {
    let ready = false
    const statuses: DrawingStatusBody[] = [
      { contract_version: '1.1', id: 'up-1', status: 'processing', progress: 0.5 },
      { contract_version: '1.1', id: 'up-1', status: 'ready' },
    ]
    const store = createAppStore({
      pollMs: 1,
      source: fakeSource({
        drawing: async () => {
          if (!ready) throw processing()
          return other
        },
        status: async () => {
          const s = statuses.shift()!
          if (s.status === 'ready') ready = true
          return s
        },
      }),
    })
    await store.getState().ask('How many windows are drawn on this floor plan?')
    expect(store.getState().turns).toHaveLength(2)
    await store.getState().loadDrawing('up-1')
    expect(store.getState().session.load).toEqual({ id: 'up-1', state: 'ready' })
    expect(store.getState().session.drawing.title).toBe('Upload')
    expect(store.getState().turns).toHaveLength(0)
    expect(store.getState().undoStack).toHaveLength(0)
  })

  it('reports failed processing with its reason, and unknown ids as not found', async () => {
    const store = createAppStore({
      source: fakeSource({
        drawing: async (id) => {
          if (id === 'gone') throw new ApiError('Unknown drawing', 404, 'unknown_drawing')
          throw processing()
        },
        status: async (id) => ({ contract_version: '1.1', id, status: 'failed', error: 'No entities' }),
      }),
    })
    await store.getState().loadDrawing('bad')
    expect(store.getState().session.load).toEqual({ id: 'bad', state: 'failed', message: 'No entities' })
    await store.getState().loadDrawing('gone')
    expect(store.getState().session.load).toEqual({ id: 'gone', state: 'not_found' })
  })

  it('a newer load wins over a slower older one; stopLoading ends polling', async () => {
    let release!: (d: Drawing) => void
    const store = createAppStore({
      source: fakeSource({
        drawing: (id) => (id === 'slow' ? new Promise<Drawing>((r) => (release = r)) : Promise.resolve(other)),
      }),
    })
    const slow = store.getState().loadDrawing('slow')
    await store.getState().loadDrawing('up-1')
    release({ ...other, id: 'slow', title: 'Slow' })
    await slow
    expect(store.getState().session.drawing.id).toBe('up-1')

    let polls = 0
    const polling = createAppStore({
      pollMs: 5,
      source: fakeSource({
        drawing: async () => {
          throw processing()
        },
        status: async (id) => {
          polls++
          return { contract_version: '1.1', id, status: 'processing' }
        },
      }),
    })
    void polling.getState().loadDrawing('up-2')
    await new Promise((r) => setTimeout(r, 30))
    polling.getState().stopLoading()
    const at = polls
    await new Promise((r) => setTimeout(r, 30))
    expect(polls).toBeLessThanOrEqual(at + 1)
  })

  it('ask sends the loaded drawing id', async () => {
    const src = fakeSource({ drawing: async () => other })
    const sent: string[] = []
    const local = src.ask
    src.ask = (req) => {
      sent.push(req.drawing_id)
      return local(req)
    }
    const store = createAppStore({ source: src })
    await store.getState().loadDrawing('up-1')
    await store.getState().ask('How many windows are drawn on this floor plan?')
    expect(sent).toEqual(['up-1'])
  })
})

describe('local answers are only for the bundled drawing', () => {
  it('asking about another drawing while the session is local gives an error, never a number', async () => {
    const src = { ...createSource({ baseUrl: null }), hasApi: () => true, drawing: async () => other }
    const store = createAppStore({ source: src })
    await store.getState().loadDrawing('up-1')
    const id = await store.getState().ask('How many windows are drawn on this floor plan?')
    const turn = store.getState().turns.find((t) => t.id === id)
    expect(turn).toMatchObject({ status: 'error' })
    expect(turn && 'response' in turn ? turn.response : undefined).toBeUndefined()
  })
})

describe('streamed answers', () => {
  it('fold events into the turn, keep the conversation id, and finish with the answer', async () => {
    const base = createSource({ baseUrl: null })
    const seen: string[] = []
    const response = await base.ask({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })
    const src: Source = {
      ...base,
      status: () => ({ kind: 'api', state: 'ready' }),
      async chat(req, onEvent) {
        seen.push(req.conversation_id ?? '-')
        onEvent({ event: 'turn', data: { conversation_id: 'c-9', turn_id: 't' } })
        onEvent({ event: 'understood', data: { detected_lang: 'en', canonical_en: 'How many windows', route: 'template' } })
        onEvent({ event: 'tool_call', data: { node: { id: 'read', title: 'Read', tag: 'DXF', body: '', kind: 'exact' } } })
        expect(store.getState().turns.at(-1)).toMatchObject({ status: 'pending', stream: { phase: 'working' } })
        onEvent({ event: 'tool_result', data: { node_id: 'read', evidence: response.response.evidence, summary: '' } })
        return response
      },
    }
    const store = createAppStore({ source: src })
    const id = await store.getState().ask('How many windows?')
    const turn = store.getState().turns.find((t) => t.id === id)
    expect(turn).toMatchObject({ status: 'done', stream: { resolved: ['read'], understood: { canonical_en: 'How many windows' } } })
    expect(store.getState().session.conversationId).toBe('c-9')
    await store.getState().ask('and doors?')
    expect(seen).toEqual(['-', 'c-9'])
  })

  it('Stop ends a streaming answer with no number', async () => {
    const base = createSource({ baseUrl: null })
    const src: Source = {
      ...base,
      chat: (_req, _on, signal) =>
        new Promise((_r, reject) => signal?.addEventListener('abort', () => reject(new DOMException('Stopped', 'AbortError')))),
    }
    const store = createAppStore({ source: src })
    const p = store.getState().ask('How many windows?')
    store.getState().cancel()
    const id = await p
    const turn = store.getState().turns.find((t) => t.id === id)
    expect(turn).toMatchObject({ status: 'error', error: { code: 'cancelled' } })
    expect(turn && 'response' in turn ? turn.response : undefined).toBeUndefined()
  })

  it('restores a conversation after a reload', async () => {
    const base = createSource({ baseUrl: null })
    const r = await base.ask({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })
    localStorage.setItem('aura.conversation.swiss-46639', 'c-5')
    const src: Source = {
      ...base,
      conversation: async (id) => ({ contract_version: '1.1', id, drawing_id: 'swiss-46639', turns: [{ turn_id: 't', message: 'How many windows?', response: r.response }] }),
    }
    const store = createAppStore({ source: src })
    await store.getState().restoreConversation()
    expect(store.getState().turns.map((t) => t.role)).toEqual(['user', 'agent'])
    expect(store.getState().session.conversationId).toBe('c-5')
    store.getState().newConversation()
    expect(store.getState().turns).toHaveLength(0)
    expect(localStorage.getItem('aura.conversation.swiss-46639')).toBeNull()
  })
})

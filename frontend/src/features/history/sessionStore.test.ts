import { describe, expect, it } from 'vitest'
import type { Drawing } from '@/api/contract'
import { ApiError } from '@/api/client'
import { createSource, type Source } from '@/api/source'
import { bundledDrawing } from '@/data/drawing'
import { createAppStore, type AgentTurn } from '@/store/appStore'
import {
  MAX_SESSIONS,
  SESSIONS_KEY,
  createSessionStore,
  groupSessions,
  sessionCsv,
  sessionMarkdown,
  type StoredSession,
} from './sessionStore'

const memStorage = () => {
  const m = new Map<string, string>()
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    map: m,
  }
}

const setup = () => {
  const app = createAppStore({ source: createSource({ baseUrl: null }) })
  const storage = memStorage()
  let t = Date.parse('2026-11-14T12:00:00')
  const hist = createSessionStore({ app, storage, now: () => (t += 1000) })
  return { app, storage, hist }
}

describe('session history', () => {
  it('archives the thread with an automatic title and turn ids that cannot collide', async () => {
    const { app, storage, hist } = setup()
    await app.getState().ask('How many windows are drawn on this floor plan?')
    const id = hist.archiveCurrent()
    expect(id).toBeTruthy()
    const s = hist.get(id!)!
    expect(s.title).toMatch(/^How many windows .* · 12 /)
    expect(s.turns.map((x) => x.id)).toEqual([`${id}-t0`, `${id}-t1`])
    expect((s.turns[1] as AgentTurn).replyTo).toBe(`${id}-t0`)
    expect(s.activeTurnId).toBe(`${id}-t1`)
    expect(JSON.parse(storage.getItem(SESSIONS_KEY)!)).toHaveLength(1)
    expect(hist.store.getState().currentId).toBeNull()
    // An empty thread archives nothing.
    app.getState().newConversation()
    expect(hist.archiveCurrent()).toBeNull()
  })

  it('restores turns, active answer and rejected boxes onto the plan', async () => {
    const { app, hist } = setup()
    const turnId = await app.getState().ask('How many windows are drawn on this floor plan?')
    const box = (app.getState().turns.find((x) => x.id === turnId) as AgentTurn).response!.evidence.boxes[0]
    app.getState().toggle(turnId, box.id)
    const id = hist.archiveCurrent()!
    expect(hist.get(id)!.rejectedBoxIds).toEqual([box.id])
    app.getState().newConversation()
    expect(app.getState().turns).toHaveLength(0)

    expect(await hist.restore(id)).toBe(true)
    const st = app.getState()
    expect(st.turns).toHaveLength(2)
    expect(st.ui.activeTurnId).toBe(`${id}-t1`)
    expect((st.turns[1] as AgentTurn).toggles.rejected[box.id]).toBe(true)
    expect(hist.store.getState().currentId).toBe(id)
    // A follow-up keeps updating the same session.
    await app.getState().ask('What is the total area?')
    hist.saveCurrent()
    expect(hist.store.getState().sessions).toHaveLength(1)
    expect(hist.get(id)!.turns).toHaveLength(4)
  })

  it('live sync keeps a cleared thread and re-attaches it on Undo', async () => {
    const { app, hist } = setup()
    const stop = hist.startSync()
    await app.getState().ask('How many windows are drawn on this floor plan?')
    const undo = app.getState().newConversation()
    const [saved] = hist.store.getState().sessions
    expect(saved.turns).toHaveLength(2)
    expect(hist.store.getState().currentId).toBeNull()
    undo()
    expect(hist.store.getState().currentId).toBe(saved.id)
    // Explicit archive followed by the clear it precedes must not save the thread twice.
    hist.archiveCurrent()
    app.getState().newConversation()
    expect(hist.store.getState().sessions.map((s) => s.id)).toEqual([saved.id])
    stop()
  })

  it('pins, renames (blank resets the auto title) and deletes with undo', async () => {
    const { app, hist } = setup()
    await app.getState().ask('How many windows are drawn on this floor plan?')
    const id = hist.archiveCurrent()!
    const auto = hist.get(id)!.title
    hist.togglePin(id)
    hist.rename(id, '  North   windows ')
    expect(hist.get(id)).toMatchObject({ pinned: true, title: 'North windows', customTitle: true })
    expect(groupSessions(hist.store.getState().sessions, Date.now())[0].key).toBe('pinned')
    hist.rename(id, '   ')
    expect(hist.get(id)).toMatchObject({ title: auto, customTitle: false })
    const undo = hist.remove(id)
    expect(hist.get(id)).toBeUndefined()
    undo()
    expect(hist.get(id)?.pinned).toBe(true)
  })

  it('caps the archive, evicting the oldest unpinned session first', () => {
    const storage = memStorage()
    const base: Omit<StoredSession, 'id' | 'pinned' | 'updated_at'> = {
      title: 't',
      customTitle: false,
      drawing_id: 'd',
      project_id: null,
      turns: [{ id: 'x', role: 'user', text: 'q', lang: 'en' }],
      activeTurnId: null,
      rejectedBoxIds: [],
      conversation_id: null,
      created_at: 0,
    }
    const many = Array.from({ length: MAX_SESSIONS + 3 }, (_, i) => ({
      ...base,
      id: `s${i}`,
      pinned: i === 0,
      updated_at: i,
    }))
    storage.setItem(SESSIONS_KEY, JSON.stringify(many))
    const app = createAppStore({ source: createSource({ baseUrl: null }) })
    const hist = createSessionStore({ app, storage })
    hist.togglePin('s5') // any write applies the cap
    const ids = hist.store.getState().sessions.map((s) => s.id)
    expect(ids).toHaveLength(MAX_SESSIONS)
    expect(ids).toContain('s0') // pinned, though oldest
    expect(ids).not.toContain('s1')
    expect(ids).not.toContain('s3')
    expect(ids).toContain('s4')
  })

  it('exports Markdown and CSV', async () => {
    const { app, hist } = setup()
    await app.getState().ask('How many windows are drawn on this floor plan?')
    const s = hist.get(hist.archiveCurrent()!)!
    expect(sessionMarkdown(s)).toContain('## 1. How many windows are drawn on this floor plan?')
    const csv = sessionCsv(s).split('\n')
    expect(csv[0]).toBe('n,question,answer,value,unit,method,boxes,rooms,rejected,cost_usd')
    expect(csv[1]).toMatch(/^1,How many windows.*,12,/)
  })

  it('restores across drawings once the target is ready, and fails cleanly on a missing one', async () => {
    const other: Drawing = { ...bundledDrawing, id: 'other-plan', title: 'Other' }
    const base = createSource({ baseUrl: null })
    const src: Source = {
      ...base,
      hasApi: () => true,
      drawing: async (id) => {
        if (id === 'other-plan') return other
        throw new ApiError('gone', 404, 'unknown_drawing')
      },
    }
    const app = createAppStore({ source: src })
    const hist = createSessionStore({ app, storage: memStorage() })
    const stop = hist.startSync()
    await app.getState().ask('How many windows are drawn on this floor plan?')
    const id = hist.archiveCurrent()!
    await app.getState().loadDrawing('other-plan')
    expect(app.getState().turns).toHaveLength(0)

    expect(await hist.restore(id)).toBe(true) // no navigate: falls back to loadDrawing
    const st = app.getState()
    expect(st.session.drawing.id).toBe('swiss-46639')
    expect(st.turns).toHaveLength(2)
    expect(st.ui.activeTurnId).toBe(`${id}-t1`)
    expect(hist.store.getState().currentId).toBe(id)
    expect(hist.store.getState().sessions).toHaveLength(1)

    const gone = { ...hist.get(id)!, id: 'sgone', drawing_id: 'gone-plan' }
    hist.store.setState({ sessions: [...hist.store.getState().sessions, gone] })
    expect(await hist.restore('sgone')).toBe(false)
    expect(hist.store.getState().restoring).toBeNull()
    stop()
  })

  it('a thread the server gives back after a reload joins its saved session instead of duplicating it', async () => {
    const { app, hist } = setup()
    await app.getState().ask('How many windows are drawn on this floor plan?')
    app.setState((s) => ({ session: { ...s.session, conversationId: 'conv-1' } }))
    const id = hist.archiveCurrent()!
    // Reload: same conversation, fresh turn objects without the user's toggles.
    const fresh = app.getState().turns.map((x) => ({ ...x, id: `r-${x.id}` }))
    app.setState({ turns: fresh })
    expect(hist.saveCurrent()).toBe(id)
    expect(hist.store.getState().sessions).toHaveLength(1)
  })

  it('deleting the open session does not save it straight back', async () => {
    const { app, hist } = setup()
    await app.getState().ask('How many windows are drawn on this floor plan?')
    const id = hist.saveCurrent()!
    const undo = hist.remove(id)
    expect(hist.saveCurrent()).toBeNull()
    expect(hist.store.getState().sessions).toHaveLength(0)
    undo()
    expect(hist.store.getState().currentId).toBe(id)
    await app.getState().ask('What is the total area?')
    expect(hist.saveCurrent()).toBe(id)
  })

  it('mirrors with a 128-bit CSPRNG client id in the X-Aura-Client header, never in the URL or body', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const fetchImpl = (async (url: string, init?: RequestInit) => {
      calls.push({ url, init })
      return new Response(JSON.stringify({ sessions: [] }), { status: 200 })
    }) as typeof fetch
    const storage = memStorage()
    storage.setItem('aura.sessions.client', 'cweak123') // old Math.random id: replaced
    const app = createAppStore({ source: createSource({ baseUrl: null }) })
    const hist = createSessionStore({ app, storage, remoteBase: 'http://api.test/', fetchImpl })
    await app.getState().ask('How many windows are drawn on this floor plan?')
    const id = hist.archiveCurrent()!
    hist.remove(id)
    await hist.pullRemote()
    const clientId = storage.getItem('aura.sessions.client')!
    expect(clientId).toMatch(/^[0-9a-f]{32}$/)
    expect(calls.map((c) => `${c.init?.method ?? 'GET'} ${c.url}`)).toEqual([
      'POST http://api.test/sessions',
      `DELETE http://api.test/sessions/${id}`,
      'GET http://api.test/sessions',
    ])
    for (const c of calls) {
      expect((c.init?.headers as Record<string, string>)['X-Aura-Client']).toBe(clientId)
      expect(c.url).not.toContain(clientId)
      expect(String(c.init?.body ?? '')).not.toContain(clientId)
    }
  })
})

import { beforeEach, describe, expect, it } from 'vitest'
import { CONTRACT_VERSION, type ProjectBundle } from './contract'
import { deleteProject, listProjects, saveProject } from './client'
import { PROJECT_TOKEN_HEADER, myProjects, myUploads } from './library'

const bundle = (id: string, ids: string[]): ProjectBundle => ({
  id,
  title: id,
  floors: ids.map((d, i) => ({ drawing_id: d, label: `F${i}`, order: i })),
  auto_matched: false,
})

beforeEach(() => localStorage.clear())

describe('project API through the shared client', () => {
  it('sends per-call headers, keeps tokens out of the body and returns the token', async () => {
    const seen: { method: string; url: string; headers: Record<string, string>; body?: string }[] = []
    const fetch = async (url: string, init?: RequestInit) => {
      seen.push({
        method: init?.method ?? 'GET',
        url,
        headers: (init?.headers ?? {}) as Record<string, string>,
        body: init?.body as string | undefined,
      })
      if (init?.method === 'POST') return new Response(JSON.stringify({ ...bundle('p1', ['a', 'b']), token: 'tok-1' }))
      if (init?.method === 'DELETE') return new Response(null, { status: 204 })
      return new Response(JSON.stringify({ projects: [bundle('p1', ['a', 'b'])] }))
    }
    const opts = { baseUrl: 'http://x', fetch }
    const stored = { ...bundle('p1', ['a', 'b']), owner_token: 'old' } as ProjectBundle
    const saved = await saveProject(opts, stored, { headers: { 'x-test': '1' } })
    expect(saved.token).toBe('tok-1')
    expect(saved.project).toEqual(bundle('p1', ['a', 'b']))
    expect(JSON.parse(seen[0].body!)).not.toHaveProperty('owner_token')
    expect(seen[0].headers['x-test']).toBe('1')
    await deleteProject(opts, 'p1', { headers: { 'x-test': '2' } })
    expect(seen[1]).toMatchObject({ method: 'DELETE', url: 'http://x/projects/p1', headers: { 'x-test': '2' } })
    expect(await listProjects(opts)).toHaveLength(1)
  })
})

describe('myProjects tokens and pruning', () => {
  it('stores the create token with the project and keeps it across saves and server syncs', () => {
    myProjects.save(bundle('p1', ['a', 'b']))
    myProjects.setToken('p1', 'tok-1')
    myProjects.save(bundle('p1', ['a', 'b', 'c']))
    expect(myProjects.tokenOf('p1')).toBe('tok-1')
    expect(myProjects.authHeaders(myProjects.get('p1')!)).toEqual({ [PROJECT_TOKEN_HEADER]: 'tok-1' })
  })

  it('saving sends the project token plus the delete token of every upload floor', () => {
    myUploads.add({ id: 'up-1', name: 'eg.pdf', delete_token: 'del-1', created_at: '2026-11-14T00:00:00Z' })
    myUploads.add({ id: 'up-2', name: 'og.pdf', delete_token: 'del-2', created_at: '2026-11-14T00:00:00Z' })
    expect(myProjects.authHeaders(bundle('p2', ['up-1', 'x', 'up-2']))).toEqual({ 'x-delete-token': 'del-1,del-2' })
    expect(myProjects.authHeaders(bundle('p3', ['x', 'y']))).toEqual({})
    myProjects.save(bundle('p4', ['up-1', 'x']))
    myProjects.setToken('p4', 'tok-4')
    expect(myProjects.authHeaders(myProjects.get('p4')!)).toEqual({
      [PROJECT_TOKEN_HEADER]: 'tok-4',
      'x-delete-token': 'del-1',
    })
  })

  it('listing sends every stored project token and every upload delete token, omitting empty ones', () => {
    expect(myProjects.listHeaders()).toEqual({})
    myProjects.save(bundle('p1', ['a', 'b']))
    myProjects.setToken('p1', 'tok-1')
    myProjects.save(bundle('p2', ['c', 'd']))
    myProjects.setToken('p2', 'tok-2')
    expect(myProjects.listHeaders()).toEqual({ [PROJECT_TOKEN_HEADER]: 'tok-2,tok-1' })
    myUploads.add({ id: 'up-1', name: 'eg.pdf', delete_token: 'del-1', created_at: '2026-11-14T00:00:00Z' })
    expect(myProjects.listHeaders()['x-delete-token']).toBe('del-1')
  })

  it('the source sends the list credentials on GET /projects', async () => {
    const { createSource } = await import('./source')
    myProjects.save(bundle('p1', ['a', 'b']))
    myProjects.setToken('p1', 'tok-1')
    let headers: Record<string, string> = {}
    const src = createSource({
      baseUrl: 'http://x',
      fetch: async (url: string, init?: RequestInit) => {
        if (url.endsWith('/projects')) {
          headers = (init?.headers ?? {}) as Record<string, string>
          return new Response(JSON.stringify({ projects: [] }))
        }
        return new Response(JSON.stringify({ ok: true, contract_version: CONTRACT_VERSION }))
      },
    })
    await src.projects?.()
    expect(headers[PROJECT_TOKEN_HEADER]).toBe('tok-1')
  })

  it('sends project & upload tokens on POST /chat and POST /ask, and auto-syncs local projects on 404 not_found', async () => {
    const { createSource } = await import('./source')
    const { ask } = await import('./local')
    const baseAns = { ...ask({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' }), source: 'api' as const }
    const sse = `event: answer\ndata: ${JSON.stringify({ response: baseAns })}\n\nevent: done\ndata: {}\n\n`

    myUploads.add({ id: 'up-1', name: 'f1.png', delete_token: 'del-1', created_at: '2026-11-15T00:00:00Z' })
    myUploads.add({ id: 'up-2', name: 'f2.png', delete_token: 'del-2', created_at: '2026-11-15T00:00:00Z' })
    myProjects.save(bundle('proj-mwgxu9xx', ['up-1', 'up-2']))
    // Simulate a stale local token after a server restart so first /chat returns 404 Unknown project
    myProjects.setToken('proj-mwgxu9xx', 'stale-tok')

    const calls: { method: string; url: string; headers: Record<string, string>; body?: string }[] = []
    let serverHasProject = false
    const src = createSource({
      baseUrl: 'http://x',
      fetch: async (url: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET'
        const headers = (init?.headers ?? {}) as Record<string, string>
        const body = init?.body as string | undefined
        calls.push({ method, url, headers, body })
        if (url.endsWith('/health')) {
          return new Response(JSON.stringify({ ok: true, contract_version: CONTRACT_VERSION, answerer: 'hybrid' }))
        }
        if (method === 'POST' && url.endsWith('/projects')) {
          serverHasProject = true
          return new Response(JSON.stringify({ ...bundle('proj-mwgxu9xx', ['up-1', 'up-2']), token: 'fresh-tok' }))
        }
        if (method === 'POST' && url.endsWith('/chat')) {
          if (!serverHasProject || headers[PROJECT_TOKEN_HEADER] !== 'fresh-tok') {
            return new Response(
              JSON.stringify({ contract_version: CONTRACT_VERSION, code: 'not_found', error: "Unknown project 'proj-mwgxu9xx'." }),
              { status: 404, headers: { 'content-type': 'application/json' } },
            )
          }
          return new Response(sse, { status: 200, headers: { 'content-type': 'text/event-stream' } })
        }
        if (method === 'POST' && url.endsWith('/ask')) {
          expect(headers[PROJECT_TOKEN_HEADER]).toBe('fresh-tok')
          expect(headers['x-delete-token']).toBe('del-2,del-1')
          return new Response(JSON.stringify(baseAns), { status: 200, headers: { 'content-type': 'application/json' } })
        }
        throw new Error(`unexpected ${method} ${url}`)
      },
    })

    const res = await src.chat(
      {
        drawing_id: 'up-1',
        question: 'How many doors across all floors?',
        project_id: 'proj-mwgxu9xx',
        scope: { kind: 'project', project_id: 'proj-mwgxu9xx', drawing_ids: ['up-1', 'up-2'] },
      },
      () => {},
    )
    expect(res.response.answer).toEqual(baseAns.answer)
    expect(myProjects.tokenOf('proj-mwgxu9xx')).toBe('fresh-tok')
    const chatCalls = calls.filter((c) => c.url.endsWith('/chat'))
    expect(chatCalls).toHaveLength(2)
    expect(chatCalls[0].headers[PROJECT_TOKEN_HEADER]).toBe('stale-tok')
    expect(chatCalls[0].headers['x-delete-token']).toBe('del-2,del-1')
    expect(chatCalls[1].headers[PROJECT_TOKEN_HEADER]).toBe('fresh-tok')
    expect(JSON.parse(chatCalls[1].body!).project_id).toBe('proj-mwgxu9xx')

    // Direct /ask also receives fresh project & delete tokens
    const askRes = await src.ask({
      drawing_id: 'up-1',
      question: 'How many doors?',
      project_id: 'proj-mwgxu9xx',
      scope: { kind: 'project', project_id: 'proj-mwgxu9xx', drawing_ids: ['up-1', 'up-2'] },
    })
    expect(askRes.response.answer).toEqual(baseAns.answer)
  })

  it('prunes floors that 404 and drops projects left with fewer than 2 floors', async () => {
    myProjects.save(bundle('keep', ['a', 'b', 'gone']))
    myProjects.save(bundle('drop', ['c', 'gone']))
    const changed = await myProjects.pruneExpired(async (id) => id === 'gone')
    expect(changed).toBe(2)
    expect(myProjects.list().map((p) => [p.id, p.floors.map((f) => f.drawing_id)])).toEqual([['keep', ['a', 'b']]])
  })
})

import { beforeEach, describe, expect, it } from 'vitest'
import type { ProjectBundle } from './contract'
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

  it("falls back to an upload's delete token for upload-backed projects", () => {
    myUploads.add({ id: 'up-1', name: 'eg.pdf', delete_token: 'del-1', created_at: '2026-11-14T00:00:00Z' })
    expect(myProjects.authHeaders(bundle('p2', ['x', 'up-1']))).toEqual({ 'x-delete-token': 'del-1' })
    expect(myProjects.authHeaders(bundle('p3', ['x', 'y']))).toEqual({})
  })

  it('prunes floors that 404 and drops projects left with fewer than 2 floors', async () => {
    myProjects.save(bundle('keep', ['a', 'b', 'gone']))
    myProjects.save(bundle('drop', ['c', 'gone']))
    const changed = await myProjects.pruneExpired(async (id) => id === 'gone')
    expect(changed).toBe(2)
    expect(myProjects.list().map((p) => [p.id, p.floors.map((f) => f.drawing_id)])).toEqual([['keep', ['a', 'b']]])
  })
})

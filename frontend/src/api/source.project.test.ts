import { afterEach, describe, expect, it, vi } from 'vitest'
import { CONTRACT_VERSION, type AskRequest, type ProjectBundle } from './contract'
import { ask as askLocal } from './local'
import { myProjects } from './library'
import { createSource, scopeHeaders } from './source'

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const health = () => json(200, { ok: true, contract_version: CONTRACT_VERSION })
const BUNDLE: ProjectBundle = {
  id: 'proj-mwgxu9xx',
  title: 'House',
  floors: [
    { drawing_id: 'up-a', label: 'EG', order: 0 },
    { drawing_id: 'up-b', label: '1OG', order: 1 },
  ],
}
const SCOPE = { kind: 'project' as const, project_id: BUNDLE.id, drawing_ids: ['up-a', 'up-b'] }
const REQ: AskRequest = { drawing_id: 'up-a', question: 'How many rooms?', scope: SCOPE }
const unknown = () =>
  json(404, { contract_version: CONTRACT_VERSION, error: `Unknown project '${BUNDLE.id}'.`, code: 'not_found' })

afterEach(() => localStorage.clear())

describe('project-scoped questions ("All x floors")', () => {
  it('send the stored project token', () => {
    myProjects.save(BUNDLE)
    myProjects.setToken(BUNDLE.id, 'tok-1')
    expect(scopeHeaders(SCOPE)).toEqual({ 'x-project-token': 'tok-1' })
    expect(scopeHeaders(undefined)).toEqual({})
  })

  it('/ask: a 404 unknown project re-saves the project once, then the question is answered', async () => {
    myProjects.save(BUNDLE)
    const answer = { ...askLocal({ drawing_id: 'swiss-46639', question: 'How many rooms?' }), source: 'api' }
    let asks = 0
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/health')) return health()
      if (url.endsWith('/projects'))
        return json(200, { contract_version: CONTRACT_VERSION, ...BUNDLE, token: 'tok-new' })
      asks++
      const h = (init?.headers ?? {}) as Record<string, string>
      return h['x-project-token'] === 'tok-new' ? json(200, answer) : unknown()
    })
    const s = createSource({ baseUrl: 'https://api.test', fetch })
    await s.init()
    const r = await s.ask(REQ)
    expect(r.response.source).toBe('api')
    expect(asks).toBe(2)
    expect(myProjects.tokenOf(BUNDLE.id)).toBe('tok-new')
  })

  it('/chat: a 404 with an error body is an ApiError (not "chat unavailable"), and the retry streams', async () => {
    myProjects.save(BUNDLE)
    const answer = { ...askLocal({ drawing_id: 'swiss-46639', question: 'How many rooms?' }), source: 'api' }
    const sse = [
      `event: turn\ndata: ${JSON.stringify({ conversation_id: 'c1', turn_id: 't1' })}\n\n`,
      `event: answer\ndata: ${JSON.stringify({ response: answer })}\n\n`,
      `event: done\ndata: {}\n\n`,
    ].join('')
    const paths: string[] = []
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      paths.push(new URL(url).pathname)
      if (url.endsWith('/health')) return health()
      if (url.endsWith('/projects'))
        return json(200, { contract_version: CONTRACT_VERSION, ...BUNDLE, token: 'tok-new' })
      const h = (init?.headers ?? {}) as Record<string, string>
      if (h['x-project-token'] !== 'tok-new') return unknown()
      return new Response(sse, { status: 200, headers: { 'content-type': 'text/event-stream' } })
    })
    const s = createSource({ baseUrl: 'https://api.test', fetch })
    await s.init()
    const r = await s.chat({ drawing_id: 'up-a', question: 'How many rooms?', scope: SCOPE }, () => undefined)
    expect(r.response.source).toBe('api')
    expect(paths.filter((p) => p === '/ask')).toEqual([])
    expect(paths.filter((p) => p === '/chat').length).toBe(2)
  })
})

import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './client'
import { CONTRACT_VERSION, type AskRequest } from './contract'
import { ask as askLocal } from './local'
import { createSource } from './source'

const REQ: AskRequest = { drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' }
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const health = () => json(200, { ok: true, contract_version: CONTRACT_VERSION })

/** Routes /health and /ask to handlers. */
function mockFetch(onAsk: () => Promise<Response>, onHealth: () => Promise<Response> = async () => health()) {
  return vi.fn((url: string) => (url.endsWith('/health') ? onHealth() : onAsk()))
}

afterEach(() => vi.useRealTimers())

describe('source', () => {
  it('no API URL → local immediately, ready, no fetch', async () => {
    const fetch = vi.fn()
    const s = createSource({ baseUrl: null, fetch })
    expect(s.status()).toEqual({ kind: 'local', state: 'ready', reason: 'no_api_url' })
    const r = await s.ask(REQ)
    expect(r.fellBack).toBe(false)
    expect(r.response.source).toBe('local')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('healthy API → api; answers come from the API', async () => {
    const apiResponse = { ...askLocal(REQ), source: 'api' }
    const fetch = mockFetch(async () => json(200, apiResponse))
    const s = createSource({ baseUrl: 'https://api.test/', fetch })
    expect(s.status().state).toBe('waking')
    expect(await s.init()).toEqual({ kind: 'api', state: 'ready' })
    const r = await s.ask(REQ)
    expect(r.response.source).toBe('api')
    expect(fetch).toHaveBeenCalledWith('https://api.test/ask', expect.objectContaining({ method: 'POST' }))
  })

  it('failed /health → local for the session; retry() can switch to api', async () => {
    let up = false
    const fetch = mockFetch(
      async () => json(200, { ...askLocal(REQ), source: 'api' }),
      async () => (up ? health() : json(503, {})),
    )
    const s = createSource({ baseUrl: 'https://api.test', fetch })
    expect(await s.init()).toMatchObject({ kind: 'local', reason: 'health_failed' })
    up = true
    expect(await s.retry()).toEqual({ kind: 'api', state: 'ready' })
  })

  it('4xx → ApiError, no fallback', async () => {
    const fetch = mockFetch(async () =>
      json(400, { contract_version: CONTRACT_VERSION, error: 'Question is empty', code: 'bad_request' }),
    )
    const s = createSource({ baseUrl: 'https://api.test', fetch })
    await s.init()
    await expect(s.ask(REQ)).rejects.toBeInstanceOf(ApiError)
    expect(s.status().kind).toBe('api')
  })

  it('FastAPI 422 {detail} → ApiError', async () => {
    const fetch = mockFetch(async () => json(422, { detail: [{ loc: ['body', 'question'], msg: 'field required' }] }))
    const s = createSource({ baseUrl: 'https://api.test', fetch })
    await s.init()
    await expect(s.ask(REQ)).rejects.toMatchObject({ name: 'ApiError', status: 422, code: 'validation' })
  })

  it('contract major mismatch → ApiError', async () => {
    const fetch = mockFetch(async () => json(200, { ...askLocal(REQ), source: 'api', contract_version: '2.0' }))
    const s = createSource({ baseUrl: 'https://api.test', fetch })
    await s.init()
    await expect(s.ask(REQ)).rejects.toMatchObject({ code: 'contract_mismatch' })
  })

  it('503 → answered locally, session switches to local', async () => {
    const fetch = mockFetch(async () => json(503, { detail: 'cold' }))
    const s = createSource({ baseUrl: 'https://api.test', fetch })
    await s.init()
    const r = await s.ask(REQ)
    expect(r.fellBack).toBe(true)
    expect(r.response.source).toBe('local')
    expect(r.response.answer).toMatchObject({ kind: 'count', value: 12 })
    expect(s.status()).toMatchObject({ kind: 'local', reason: 'fell_back' })
    // Not mixed: the next ask stays local without calling /ask.
    fetch.mockClear()
    await s.ask(REQ)
    expect(fetch).not.toHaveBeenCalled()
  })

  it('network error → local fallback', async () => {
    const fetch = mockFetch(async () => {
      throw new TypeError('Failed to fetch')
    })
    const s = createSource({ baseUrl: 'https://api.test', fetch })
    await s.init()
    expect((await s.ask(REQ)).fellBack).toBe(true)
  })

  it('no fallback for drawings other than the bundled one', async () => {
    const fetch = mockFetch(async () => json(503, {}))
    const s = createSource({ baseUrl: 'https://api.test', fetch })
    await s.init()
    await expect(s.ask({ ...REQ, drawing_id: 'other' })).rejects.toMatchObject({ name: 'TransportError' })
  })

  it('a response arriving after the timeout is ignored', async () => {
    let lateResolved = false
    const late = { ...askLocal(REQ), source: 'api', answer: { kind: 'count', value: 999, subject: 'window' } }
    // This fetch ignores the abort signal on purpose.
    const fetch = mockFetch(
      () =>
        new Promise<Response>((resolve) =>
          setTimeout(() => {
            lateResolved = true
            resolve(json(200, late))
          }, 60),
        ),
    )
    const s = createSource({ baseUrl: 'https://api.test', fetch, askTimeoutMs: 20 })
    await s.init()
    const r = await s.ask(REQ)
    expect(r.fellBack).toBe(true)
    expect(r.response.answer).toMatchObject({ value: 12 })
    await new Promise((res) => setTimeout(res, 80))
    expect(lateResolved).toBe(true)
    expect(r.response.answer).toMatchObject({ value: 12 })
    expect(s.status().kind).toBe('local')
  })

  it('aborts the request on timeout', async () => {
    let signal: AbortSignal | undefined
    const fetch = vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith('/health')) return Promise.resolve(health())
      signal = init?.signal ?? undefined
      return new Promise<Response>(() => undefined)
    })
    const s = createSource({ baseUrl: 'https://api.test', fetch, askTimeoutMs: 10 })
    await s.init()
    await s.ask(REQ)
    expect(signal?.aborted).toBe(true)
  })

  it('/health timeout → local', async () => {
    const fetch = mockFetch(
      async () => json(200, {}),
      () => new Promise<Response>(() => undefined),
    )
    const s = createSource({ baseUrl: 'https://api.test', fetch, healthTimeoutMs: 10 })
    expect(await s.init()).toMatchObject({ kind: 'local', reason: 'health_failed' })
  })
})

describe('source with VITE_API_URL', () => {
  afterEach(() => vi.unstubAllEnvs())

  it('empty VITE_API_URL → local, ready, no fetch', async () => {
    vi.stubEnv('VITE_API_URL', '')
    const fetch = vi.fn()
    const s = createSource({ fetch })
    expect(s.status()).toEqual({ kind: 'local', state: 'ready', reason: 'no_api_url' })
    await s.init()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('set VITE_API_URL → waking until /health answers', async () => {
    vi.stubEnv('VITE_API_URL', 'https://api.test')
    const fetch = mockFetch(async () => json(200, {}))
    const s = createSource({ fetch })
    expect(s.status().state).toBe('waking')
    expect(await s.init()).toEqual({ kind: 'api', state: 'ready' })
    expect(fetch).toHaveBeenCalledWith('https://api.test/health', expect.anything())
  })
})

describe('null optionals from the API', () => {
  it('are treated as absent', async () => {
    const body = { ...askLocal(REQ), source: 'api', gold: null, check: null }
    const fetch = mockFetch(async () => json(200, body))
    const s = createSource({ baseUrl: 'https://api.test', fetch })
    await s.init()
    const r = await s.ask(REQ)
    expect(r.response.gold).toBeUndefined()
    expect(r.response.check).toBeUndefined()
  })
})

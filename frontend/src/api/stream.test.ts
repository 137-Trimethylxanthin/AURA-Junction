import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, TransportError, deleteProject, listProjects, saveProject } from './client'
import type { ChatEvent } from './contract'
import { ask } from './local'
import { createSource } from './source'
import { ChatUnavailable, splitFrames, streamChat, toChatEvent } from './stream'

const answer = ask({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
const node = { id: 'read', title: 'Read the layer', tag: 'DXF', body: '12 windows', kind: 'exact', evidence_ids: ['win-01'], tool: { name: 'count_objects', args: {}, result_id: 'r1', ms: 3 } }
const STREAM = [
  frame('turn', { conversation_id: 'c-1', turn_id: 't-1' }),
  ': keep-alive comment\n\n',
  frame('understood', { detected_lang: 'en', canonical_en: 'How many windows', route: 'template' }),
  frame('tool_call', { node }),
  frame('tool_result', { node_id: 'read', evidence: { boxes: answer.evidence.boxes.slice(0, 1), rooms: [] }, summary: '12' }),
  frame('mystery', { ignored: true }),
  frame('answer', { response: { ...answer, source: 'api' } }),
  frame('done', {}),
].join('')

/** A fetch that answers with `body`, split into small chunks so frames cross chunk boundaries. */
function fakeFetch(body: string, init: { status?: number; type?: string } = {}) {
  return async () => {
    const bytes = new TextEncoder().encode(body)
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        for (let i = 0; i < bytes.length; i += 37) c.enqueue(bytes.slice(i, i + 37))
        c.close()
      },
    })
    return new Response(stream, { status: init.status ?? 200, headers: { 'content-type': init.type ?? 'text/event-stream' } })
  }
}

describe('splitFrames', () => {
  it('keeps the unfinished tail and joins multi-line data', () => {
    const { frames, rest } = splitFrames('event: a\ndata: {"x":\ndata: 1}\n\nevent: b\ndata: {')
    expect(frames).toEqual([{ event: 'a', data: '{"x":\n1}' }])
    expect(rest).toBe('event: b\ndata: {')
  })
  it('accepts CRLF line ends', () => {
    expect(splitFrames('event: done\r\ndata: {}\r\n\r\n').frames).toEqual([{ event: 'done', data: '{}' }])
  })
})

describe('streamChat', () => {
  const req = { drawing_id: 'swiss-46639', message: 'How many windows?' }
  it('delivers the events in order across chunk boundaries and stops at done', async () => {
    const got: ChatEvent['event'][] = []
    await streamChat(req, { baseUrl: 'http://x', fetch: fakeFetch(STREAM), onEvent: (e) => got.push(e.event) })
    expect(got).toEqual(['turn', 'understood', 'tool_call', 'tool_result', 'answer', 'done'])
  })
  it('a reply that is not an event stream means /chat is unavailable', async () => {
    await expect(
      streamChat(req, { baseUrl: 'http://x', fetch: fakeFetch('<!doctype html>', { type: 'text/html' }), onEvent: () => {} }),
    ).rejects.toBeInstanceOf(ChatUnavailable)
    await expect(
      streamChat(req, { baseUrl: 'http://x', fetch: fakeFetch('', { status: 404 }), onEvent: () => {} }),
    ).rejects.toBeInstanceOf(ChatUnavailable)
  })
  it('4xx is an ApiError, 5xx a TransportError', async () => {
    const body = JSON.stringify({ contract_version: '1.1', error: 'Slow down', code: 'rate_limited' })
    await expect(
      streamChat(req, { baseUrl: 'http://x', fetch: fakeFetch(body, { status: 429, type: 'application/json' }), onEvent: () => {} }),
    ).rejects.toMatchObject({ code: 'rate_limited' })
    await expect(
      streamChat(req, { baseUrl: 'http://x', fetch: fakeFetch('', { status: 503 }), onEvent: () => {} }),
    ).rejects.toBeInstanceOf(TransportError)
  })
  it('a malformed known event is an error, never a silent number', async () => {
    const bad = frame('answer', { response: { answer: { kind: 'count', value: 12 } } })
    await expect(streamChat(req, { baseUrl: 'http://x', fetch: fakeFetch(bad), onEvent: () => {} })).rejects.toBeInstanceOf(ApiError)
  })
  it('aborts when the caller aborts', async () => {
    const ctrl = new AbortController()
    const never = async (_: string, init?: RequestInit) =>
      new Promise<Response>((_r, reject) => init?.signal?.addEventListener('abort', () => reject(new DOMException('Stopped', 'AbortError'))))
    const p = streamChat(req, { baseUrl: 'http://x', fetch: never, signal: ctrl.signal, onEvent: () => {} })
    ctrl.abort()
    await expect(p).rejects.toMatchObject({ name: 'AbortError' })
  })
})

describe('toChatEvent', () => {
  it('accepts a plain sample check (DXF upload, IFC) as well as a ranked one', () => {
    expect(toChatEvent('check', { check: { method: 'decisions_api', status: 'sample', image: 'none' } })).not.toBeNull()
    expect(toChatEvent('check', { check: { method: 'decisions_api', status: 'live', scope: 'drawn_boxes', ranking: ['win-01'] } })).not.toBeNull()
    expect(() => toChatEvent('check', { check: { method: 'guess' } })).toThrow()
  })

  it('validates 1.2 progress queue_position events and rejects negative or non-numeric ones', () => {
    expect(toChatEvent('progress', { queue_position: 2 })).toEqual({ event: 'progress', data: { queue_position: 2 } })
    expect(toChatEvent('progress', { queue_position: 0 })).toEqual({ event: 'progress', data: { queue_position: 0 } })
    expect(() => toChatEvent('progress', { queue_position: -1 })).toThrow(ApiError)
    expect(() => toChatEvent('progress', { queue_position: Number.NaN })).toThrow(ApiError)
    expect(() => toChatEvent('progress', {})).toThrow(ApiError)
  })
})

describe('1.2 SSE heartbeat keep-alive & deep_agent streaming', () => {
  const req = { drawing_id: 'swiss-46639', message: 'What is the width of the narrowest doorway?' }

  afterEach(() => {
    vi.useRealTimers()
  })

  it('keeps a 90s agent stream alive past the 75s idle timeout when : progress heartbeats arrive every 15s', async () => {
    vi.useFakeTimers()
    const enc = new TextEncoder()
    const agentAnswer = {
      ...answer,
      contract_version: '1.2',
      answer: { kind: 'note' as const, value: '0.78 m clear doorway width in room 3.02', number: 0.78, unit: 'm' },
      method: 'agent_estimate' as const,
      route: 'agent' as const,
      calls: 4,
      cost_usd: 0.12,
      estimate: { confidence: 0.85 },
      source: 'api' as const,
    }
    const agentNode = {
      id: 'agent-1',
      title: 'Inspecting doorway openings',
      tag: 'AGENT',
      body: 'Measured jambs',
      kind: 'model' as const,
      tool: { name: 'deep_agent' as const, args: { step: 'Inspecting doorway openings', seq: 1 }, result_id: 'ar1', ms: 1400 },
    }
    const fetchWithHeartbeats = async () => {
      const stream = new ReadableStream<Uint8Array>({
        start(c) {
          c.enqueue(enc.encode(frame('turn', { conversation_id: 'c-9', turn_id: 't-9' })))
          c.enqueue(enc.encode(frame('progress', { queue_position: 1 })))
          setTimeout(() => c.enqueue(enc.encode(': progress 15\n\n')), 15_000)
          setTimeout(() => c.enqueue(enc.encode(frame('progress', { queue_position: 0 }))), 30_000)
          setTimeout(() => c.enqueue(enc.encode(': progress 45\n\n')), 45_000)
          setTimeout(() => c.enqueue(enc.encode(frame('tool_call', { node: agentNode }))), 60_000)
          setTimeout(() => c.enqueue(enc.encode(': progress 75\n\n')), 75_000)
          setTimeout(() => {
            c.enqueue(enc.encode(frame('answer', { response: agentAnswer })))
            c.enqueue(enc.encode(frame('done', {})))
            c.close()
          }, 90_000)
        },
      })
      return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } })
    }

    const events: ChatEvent[] = []
    const p = streamChat(req, {
      baseUrl: 'http://x',
      fetch: fetchWithHeartbeats,
      idleTimeoutMs: 75_000,
      onEvent: (e) => events.push(e),
    })
    await vi.advanceTimersByTimeAsync(90_000)
    await p
    expect(events.map((e) => e.event)).toEqual(['turn', 'progress', 'progress', 'tool_call', 'answer', 'done'])
  })

  it('rejects with TransportError timeout after 75s of complete silence', async () => {
    vi.useFakeTimers()
    const silentFetch = async (_: string, init?: RequestInit) => {
      const stream = new ReadableStream<Uint8Array>({
        start(c) {
          init?.signal?.addEventListener('abort', () => c.error(new DOMException('Timed out', 'AbortError')))
        },
      })
      return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } })
    }
    const p = streamChat(req, { baseUrl: 'http://x', fetch: silentFetch, idleTimeoutMs: 75_000, onEvent: () => {} })
    const assertion = expect(p).rejects.toMatchObject({ name: 'TransportError', kind: 'timeout' })
    await vi.advanceTimersByTimeAsync(75_001)
    await assertion
  })
})

describe('1.2 /health answerer & /projects client transport', () => {
  const sampleBundle = {
    id: 'proj-villa-1',
    title: 'Villa Sunset',
    floors: [
      { drawing_id: 'up-eg', label: 'EG', order: 0 },
      { drawing_id: 'up-og1', label: '1. OG', order: 1 },
    ],
    auto_matched: true,
  }

  it('populates answerer and agentOk on SourceStatus when /health includes them, and omits them otherwise', async () => {
    const withAgent = createSource({
      baseUrl: 'http://x',
      fetch: async () =>
        new Response(JSON.stringify({ ok: true, contract_version: '1.2', answerer: 'hybrid', agent_ok: true }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    })
    expect(await withAgent.init()).toEqual({ kind: 'api', state: 'ready', answerer: 'hybrid', agentOk: true })

    const plain = createSource({
      baseUrl: 'http://x',
      fetch: async () =>
        new Response(JSON.stringify({ ok: true, contract_version: '1.2' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    })
    expect(await plain.init()).toEqual({ kind: 'api', state: 'ready' })
  })

  it('lists, saves, and deletes project bundles via /projects', async () => {
    const calls: { method: string; url: string }[] = []
    const mockFetch = async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, url })
      if (method === 'GET' && url.endsWith('/projects')) {
        return new Response(JSON.stringify({ contract_version: '1.2', projects: [sampleBundle] }), { status: 200 })
      }
      if (method === 'POST' && url.endsWith('/projects')) {
        return new Response(JSON.stringify({ contract_version: '1.2', project: sampleBundle }), { status: 200 })
      }
      if (method === 'DELETE' && url.endsWith('/projects/proj-villa-1')) {
        return new Response(JSON.stringify({ ok: true }), { status: 200 })
      }
      return new Response(JSON.stringify({ ok: true, contract_version: '1.2' }), { status: 200 })
    }

    const listed = await listProjects({ baseUrl: 'http://x', fetch: mockFetch })
    expect(listed).toEqual([sampleBundle])
    const saved = await saveProject({ baseUrl: 'http://x', fetch: mockFetch }, sampleBundle)
    expect(saved).toEqual(sampleBundle)
    await deleteProject({ baseUrl: 'http://x', fetch: mockFetch }, 'proj-villa-1')
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      'GET http://x/projects',
      'POST http://x/projects',
      'DELETE http://x/projects/proj-villa-1',
    ])
  })
})

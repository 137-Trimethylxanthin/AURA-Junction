import { describe, expect, it } from 'vitest'
import { ApiError, TransportError } from './client'
import type { ChatEvent } from './contract'
import { ask } from './local'
import { ChatUnavailable, splitFrames, streamChat } from './stream'

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

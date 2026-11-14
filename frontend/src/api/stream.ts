import { ApiError, TransportError, checkVersion, type ClientOptions, type FetchLike } from './client'
import {
  isAskResponse,
  isErrorBody,
  isCheck,
  type ChatEvent,
  type ChatRequest,
  type Conversation,
} from './contract'

/**
 * POST /chat as a server-sent event stream, read with fetch (EventSource cannot POST). No dependency.
 * Frames are `event: <name>\ndata: <json>\n\n`; unknown events are ignored, a malformed known one is an error.
 */

/** /chat is not served by this backend (an older one, or a static host): use /ask instead. */
export class ChatUnavailable extends Error {
  constructor(message = 'Streaming chat is not available on this server') {
    super(message)
    this.name = 'ChatUnavailable'
  }
}

const join = (base: string, path: string) => base.replace(/\/+$/, '') + path

/** Split an SSE buffer into complete frames; returns the frames and the unfinished rest. */
export function splitFrames(buffer: string): { frames: { event: string; data: string }[]; rest: string } {
  const norm = buffer.replace(/\r\n?/g, '\n')
  const parts = norm.split('\n\n')
  const rest = parts.pop() ?? ''
  const frames: { event: string; data: string }[] = []
  for (const part of parts) {
    let event = 'message'
    const data: string[] = []
    for (const line of part.split('\n')) {
      if (!line || line.startsWith(':')) continue
      const i = line.indexOf(':')
      const field = i < 0 ? line : line.slice(0, i)
      const value = i < 0 ? '' : line.slice(i + 1).replace(/^ /, '')
      if (field === 'event') event = value
      else if (field === 'data') data.push(value)
    }
    if (data.length) frames.push({ event, data: data.join('\n') })
  }
  return { frames, rest }
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

/** Validate one decoded frame into a ChatEvent; null for events this client does not know. */
export function toChatEvent(event: string, data: unknown): ChatEvent | null {
  const bad = () => new ApiError(`The "${event}" event does not match the contract`, 200, 'invalid_response', data)
  switch (event) {
    case 'turn':
      if (!isObj(data) || typeof data.conversation_id !== 'string' || typeof data.turn_id !== 'string') throw bad()
      return { event, data: data as never }
    case 'understood':
      if (!isObj(data) || typeof data.canonical_en !== 'string') throw bad()
      return { event, data: data as never }
    case 'tool_call':
      if (!isObj(data) || !isObj(data.node) || typeof data.node.id !== 'string') throw bad()
      return { event, data: data as never }
    case 'tool_result':
      if (!isObj(data) || typeof data.node_id !== 'string' || !isObj(data.evidence)) throw bad()
      return { event, data: data as never }
    case 'check':
      if (!isObj(data) || !isCheck(data.check)) throw bad()
      return { event, data: data as never }
    case 'answer':
      if (!isObj(data) || !isAskResponse(data.response)) throw bad()
      checkVersion(data.response.contract_version, data.response)
      return { event, data: data as never }
    case 'error':
      if (!isObj(data) || typeof data.error !== 'string') throw bad()
      return { event, data: data as never }
    case 'done':
      return { event, data: {} }
    default:
      return null
  }
}

function dropNulls(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(dropNulls)
  if (v && typeof v === 'object')
    return Object.fromEntries(
      Object.entries(v as Record<string, unknown>)
        .filter(([, x]) => x !== null)
        .map(([k, x]) => [k, dropNulls(x)]),
    )
  return v
}

export interface ChatStreamOptions extends ClientOptions {
  signal?: AbortSignal
  /** No byte for this long (connect included) → TransportError timeout. The server's own deadline is 60 s. */
  idleTimeoutMs?: number
  onEvent: (e: ChatEvent) => void
}

/**
 * Stream one chat turn. Resolves after `done` (or the end of the stream). Rejects with:
 * ChatUnavailable (404/405 or a non-SSE reply), ApiError (4xx, invalid event), TransportError (network, 5xx, idle
 * timeout), or a DOMException AbortError when the caller aborts.
 */
export async function streamChat(req: ChatRequest, opts: ChatStreamOptions): Promise<void> {
  const doFetch: FetchLike = opts.fetch ?? ((i, n) => globalThis.fetch(i, n))
  const ctrl = new AbortController()
  const onAbort = () => ctrl.abort(opts.signal?.reason)
  if (opts.signal?.aborted) onAbort()
  opts.signal?.addEventListener('abort', onAbort)
  const idleMs = opts.idleTimeoutMs ?? 75_000
  let idle: ReturnType<typeof setTimeout> | undefined
  let timedOut = false
  const touch = () => {
    clearTimeout(idle)
    idle = setTimeout(() => {
      timedOut = true
      ctrl.abort()
    }, idleMs)
  }
  try {
    touch()
    let res: Response
    try {
      res = await doFetch(join(opts.baseUrl, '/chat'), {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
        body: JSON.stringify(req),
        signal: ctrl.signal,
      })
    } catch (e) {
      if (opts.signal?.aborted) throw e
      if (timedOut) throw new TransportError(`No answer within ${idleMs / 1000} s`, 'timeout')
      throw new TransportError(e instanceof Error ? e.message : 'Network error', 'network')
    }
    if (res.status === 404 || res.status === 405) throw new ChatUnavailable()
    if (res.status >= 500) throw new TransportError(`Server error ${res.status}`, 'server', res.status)
    if (res.status >= 400) {
      let body: unknown = null
      try {
        body = dropNulls(await res.json())
      } catch {
        body = null
      }
      if (isErrorBody(body)) throw new ApiError(body.error, res.status, body.code, body)
      throw new ApiError(`Request failed (${res.status})`, res.status, 'bad_request', body)
    }
    if (!(res.headers.get('content-type') ?? '').includes('text/event-stream') || !res.body) throw new ChatUnavailable()

    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
    // Stop must end the read even when the transport does not error the body on abort.
    const stop = () => void reader.cancel().catch(() => undefined)
    if (ctrl.signal.aborted) stop()
    ctrl.signal.addEventListener('abort', stop)
    let buffer = ''
    for (;;) {
      let chunk: ReadableStreamReadResult<string>
      try {
        chunk = await reader.read()
      } catch (e) {
        if (opts.signal?.aborted) throw e
        if (timedOut) throw new TransportError(`No answer within ${idleMs / 1000} s`, 'timeout')
        throw new TransportError(e instanceof Error ? e.message : 'The stream broke off', 'network')
      }
      if (chunk.done) {
        if (opts.signal?.aborted) throw new DOMException('Stopped', 'AbortError')
        if (timedOut) throw new TransportError(`No answer within ${idleMs / 1000} s`, 'timeout')
        break
      }
      touch()
      buffer += chunk.value
      const { frames, rest } = splitFrames(buffer)
      buffer = rest
      for (const f of frames) {
        let data: unknown
        try {
          data = dropNulls(JSON.parse(f.data))
        } catch {
          throw new ApiError(`The "${f.event}" event is not JSON`, 200, 'invalid_response', f.data)
        }
        const ev = toChatEvent(f.event, data)
        if (!ev) continue
        opts.onEvent(ev)
        if (ev.event === 'done') {
          void reader.cancel().catch(() => undefined)
          return
        }
      }
    }
  } finally {
    clearTimeout(idle)
    opts.signal?.removeEventListener('abort', onAbort)
  }
}

/** GET /conversations/:id; null when the server no longer knows it (expired or never stored). */
export async function getConversation(opts: ClientOptions, id: string, timeoutMs = 15_000): Promise<Conversation | null> {
  const doFetch: FetchLike = opts.fetch ?? ((i, n) => globalThis.fetch(i, n))
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    let res: Response
    try {
      res = await doFetch(join(opts.baseUrl, `/conversations/${encodeURIComponent(id)}`), { signal: ctrl.signal })
    } catch (e) {
      throw new TransportError(e instanceof Error ? e.message : 'Network error', 'network')
    }
    if (res.status === 404) return null
    if (!res.ok) throw new TransportError(`Server error ${res.status}`, 'server', res.status)
    let body: unknown
    try {
      body = dropNulls(await res.json())
    } catch {
      return null
    }
    if (!isObj(body) || typeof body.id !== 'string' || !Array.isArray(body.turns)) return null
    const turns = body.turns.filter(
      (t): t is Conversation['turns'][number] =>
        isObj(t) &&
        typeof t.turn_id === 'string' &&
        typeof t.message === 'string' &&
        (t.response === undefined || isAskResponse(t.response)),
    )
    return { ...(body as unknown as Conversation), turns }
  } finally {
    clearTimeout(timer)
  }
}

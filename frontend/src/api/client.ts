import {
  CONTRACT_VERSION,
  isAskResponse,
  isDrawing,
  isDrawingList,
  isDrawingStatusBody,
  isErrorBody,
  isFastApiDetail,
  isHealth,
  majorOf,
  type AskRequest,
  type AskResponse,
  type Drawing,
  type DrawingList,
  type DrawingStatusBody,
  type ErrorCode,
  type Health,
} from './contract'

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>

/** A 4xx, an invalid body or a contract mismatch: shown as an error card, never a fallback. */
export class ApiError extends Error {
  readonly status: number
  readonly code: ErrorCode | 'contract_mismatch' | 'invalid_response' | 'validation'
  readonly body: unknown
  constructor(message: string, status: number, code: ApiError['code'], body?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.body = body
  }
}

/** Network error, 5xx or timeout: the caller may fall back to the local source. */
export class TransportError extends Error {
  readonly kind: 'network' | 'server' | 'timeout'
  readonly status?: number
  constructor(message: string, kind: TransportError['kind'], status?: number) {
    super(message)
    this.name = 'TransportError'
    this.kind = kind
    this.status = status
  }
}

export interface ClientOptions {
  baseUrl: string
  fetch?: FetchLike
}

/** Optional fields may arrive as null (Pydantic's default); the contract treats them as absent. */
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

const join = (base: string, path: string) => base.replace(/\/+$/, '') + path

export function checkVersion(v: string, body: unknown) {
  if (majorOf(v) !== majorOf(CONTRACT_VERSION))
    throw new ApiError(`Contract ${v} does not match the app's ${CONTRACT_VERSION}`, 200, 'contract_mismatch', body)
}

/**
 * Fetch JSON with a hard timeout. The AbortController cancels the request; the race with our own timer
 * also guarantees that a response arriving after the timeout is ignored even if fetch ignores the signal.
 */
export async function request(opts: ClientOptions, path: string, init: RequestInit, timeoutMs: number): Promise<unknown> {
  const doFetch: FetchLike = opts.fetch ?? ((i, n) => globalThis.fetch(i, n))
  const ctrl = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      ctrl.abort()
      reject(new TransportError(`No answer within ${timeoutMs / 1000} s`, 'timeout'))
    }, timeoutMs)
  })
  const call = doFetch(join(opts.baseUrl, path), { ...init, signal: ctrl.signal })
  call.catch(() => undefined) // a late rejection after the timeout must not surface as unhandled
  let res: Response
  try {
    res = await Promise.race([call, timeout])
  } catch (e) {
    if (e instanceof TransportError) throw e
    throw new TransportError(e instanceof Error ? e.message : 'Network error', 'network')
  } finally {
    clearTimeout(timer)
  }

  let body: unknown
  try {
    body = await res.json()
  } catch {
    body = null
  }
  body = dropNulls(body)
  if (res.status >= 500) throw new TransportError(`Server error ${res.status}`, 'server', res.status)
  if (res.status >= 400) {
    if (isErrorBody(body)) throw new ApiError(body.error, res.status, body.code, body)
    if (isFastApiDetail(body)) throw new ApiError('The request was not accepted', res.status, 'validation', body)
    throw new ApiError(`Request failed (${res.status})`, res.status, 'bad_request', body)
  }
  return body
}

export async function getHealth(opts: ClientOptions, timeoutMs = 10_000): Promise<Health> {
  const body = await request(opts, '/health', { method: 'GET' }, timeoutMs)
  if (!isHealth(body)) throw new ApiError('Unexpected /health body', 200, 'invalid_response', body)
  checkVersion(body.contract_version, body)
  return body
}

export async function postAsk(opts: ClientOptions, req: AskRequest, timeoutMs = 15_000): Promise<AskResponse> {
  const body = await request(
    opts,
    '/ask',
    { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(req) },
    timeoutMs,
  )
  if (body && typeof body === 'object' && typeof (body as { contract_version?: unknown }).contract_version === 'string')
    checkVersion((body as { contract_version: string }).contract_version, body)
  if (!isAskResponse(body)) throw new ApiError('The answer does not match the contract', 200, 'invalid_response', body)
  return body
}

export async function getDrawing(opts: ClientOptions, id: string, timeoutMs = 15_000): Promise<Drawing> {
  const body = await request(opts, `/drawings/${encodeURIComponent(id)}`, { method: 'GET' }, timeoutMs)
  if (!isDrawing(body)) throw new ApiError('The drawing does not match the contract', 200, 'invalid_response', body)
  checkVersion(body.contract_version, body)
  return body
}

export async function listDrawings(opts: ClientOptions, timeoutMs = 15_000): Promise<DrawingList> {
  const body = await request(opts, '/drawings', { method: 'GET' }, timeoutMs)
  if (!isDrawingList(body)) throw new ApiError('The drawing list does not match the contract', 200, 'invalid_response', body)
  checkVersion(body.contract_version, body)
  return body
}

export async function getDrawingStatus(opts: ClientOptions, id: string, timeoutMs = 15_000): Promise<DrawingStatusBody> {
  const body = await request(opts, `/drawings/${encodeURIComponent(id)}/status`, { method: 'GET' }, timeoutMs)
  if (!isDrawingStatusBody(body)) throw new ApiError('The drawing status does not match the contract', 200, 'invalid_response', body)
  checkVersion(body.contract_version, body)
  return body
}

export async function deleteDrawing(opts: ClientOptions, id: string, deleteToken: string, timeoutMs = 15_000): Promise<void> {
  await request(
    opts,
    `/drawings/${encodeURIComponent(id)}`,
    { method: 'DELETE', headers: { 'X-Delete-Token': deleteToken } },
    timeoutMs,
  )
}

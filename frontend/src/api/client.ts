import {
  CONTRACT_VERSION,
  isAskResponse,
  salvageAskResponse,
  salvageDrawing,
  salvageDrawingList,
  isDrawing,
  isDrawingList,
  isDrawingStatusBody,
  isErrorBody,
  fastApiMessage,
  isFastApiDetail,
  isHealth,
  majorOf,
  type AskRequest,
  type AskResponse,
  type Budget,
  type Drawing,
  type DrawingList,
  type DrawingStatusBody,
  type ErrorCode,
  type Health,
  type ProjectBundle,
  type ProjectFloor,
  type ProjectList,
} from './contract'

export type { ProjectBundle, ProjectFloor, ProjectList }

/** The presenter token, when this browser has one: the server lets only presenters spend model calls on uploads. */
export function presenterHeader(): Record<string, string> {
  try {
    const t = globalThis.localStorage?.getItem('aura.presenter')
    return t ? { 'X-AURA-Presenter': t } : {}
  } catch {
    return {}
  }
}

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
  readonly body?: unknown
  constructor(message: string, kind: TransportError['kind'], status?: number, body?: unknown) {
    super(message)
    this.name = 'TransportError'
    this.kind = kind
    this.status = status
    this.body = body
  }
}

export interface ClientOptions {
  baseUrl: string
  fetch?: FetchLike
}

/** Fields where null is a value ("unknown", e.g. an area that is not printed), not an absent optional field. */
const NULL_IS_VALUE = new Set(['area_m2'])

/** Optional fields may arrive as null (Pydantic's default); the contract treats them as absent. */
export function dropNulls(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(dropNulls)
  if (v && typeof v === 'object')
    return Object.fromEntries(
      Object.entries(v as Record<string, unknown>)
        .filter(([k, x]) => x !== null || NULL_IS_VALUE.has(k))
        .map(([k, x]) => [k, dropNulls(x)]),
    )
  return v
}

/**
 * Model requests already sent before a failure (504 bodies and the SSE `timeout` error carry optional
 * `calls`/`cost_usd`): they were paid for, so they count in the session total. Zero when not reported.
 */
export function spentOf(e: unknown): { calls: number; cost_usd: number } {
  const b = e instanceof ApiError || e instanceof TransportError ? e.body : undefined
  const o = (b && typeof b === 'object' ? b : {}) as { calls?: unknown; cost_usd?: unknown }
  const n = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : 0)
  return { calls: n(o.calls), cost_usd: n(o.cost_usd) }
}

export const join = (base: string, path: string) => base.replace(/\/+$/, '') + path

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
  if (res.status >= 500) throw new TransportError(`Server error ${res.status}`, 'server', res.status, body)
  if (res.status >= 400) {
    if (isErrorBody(body)) throw new ApiError(body.error, res.status, body.code, body)
    if (isFastApiDetail(body))
      throw new ApiError(`The request was not accepted: ${fastApiMessage(body) ?? `status ${res.status}`}`, res.status, 'validation', body)
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

/** `GET /budget` (1.1): the server's model-request budget. Throws on any body that is not a whole Budget. */
export async function getBudget(opts: ClientOptions, timeoutMs = 8_000): Promise<Budget> {
  const b = (await request(opts, '/budget', { method: 'GET' }, timeoutMs)) as Partial<Budget> | null
  const n = (v: unknown) => typeof v === 'number' && Number.isFinite(v) && v >= 0
  if (!b || typeof b !== 'object' || (b.models !== 'on' && b.models !== 'off') || !n(b.day_used) || !n(b.day_cap) || !n(b.event_used) || !n(b.event_cap))
    throw new ApiError('Unexpected /budget body', 200, 'invalid_response', b)
  return b as Budget
}

export async function postAsk(opts: ClientOptions, req: AskRequest, timeoutMs = 15_000): Promise<AskResponse> {
  const body = await request(
    opts,
    '/ask',
    { method: 'POST', headers: { 'content-type': 'application/json', ...presenterHeader() }, body: JSON.stringify(req) },
    timeoutMs,
  )
  if (body && typeof body === 'object' && typeof (body as { contract_version?: unknown }).contract_version === 'string')
    checkVersion((body as { contract_version: string }).contract_version, body)
  const answer = salvageAskResponse(body)
  if (!isAskResponse(answer)) throw new ApiError('The answer does not match the contract', 200, 'invalid_response', body)
  return answer
}

export async function getDrawing(opts: ClientOptions, id: string, timeoutMs = 15_000): Promise<Drawing> {
  const body = salvageDrawing(await request(opts, `/drawings/${encodeURIComponent(id)}`, { method: 'GET' }, timeoutMs))
  if (!isDrawing(body)) throw new ApiError('The drawing does not match the contract', 200, 'invalid_response', body)
  checkVersion(body.contract_version, body)
  return body
}

export async function listDrawings(opts: ClientOptions, timeoutMs = 15_000): Promise<DrawingList> {
  const { list: body, dropped } = salvageDrawingList(await request(opts, '/drawings', { method: 'GET' }, timeoutMs))
  if (!isDrawingList(body)) throw new ApiError('The drawing list does not match the contract', 200, 'invalid_response', body)
  checkVersion(body.contract_version, body)
  return dropped ? { ...body, dropped } : body
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

const isObjRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)

export function isProjectFloor(v: unknown): v is ProjectFloor {
  return (
    isObjRecord(v) &&
    typeof v.drawing_id === 'string' &&
    v.drawing_id.length > 0 &&
    typeof v.label === 'string' &&
    typeof v.order === 'number' &&
    Number.isFinite(v.order)
  )
}

export function isProjectBundle(v: unknown): v is ProjectBundle {
  return (
    isObjRecord(v) &&
    typeof v.id === 'string' &&
    v.id.length > 0 &&
    typeof v.title === 'string' &&
    Array.isArray(v.floors) &&
    v.floors.every(isProjectFloor) &&
    (v.auto_matched === undefined || typeof v.auto_matched === 'boolean') &&
    (v.created_at === undefined || typeof v.created_at === 'string')
  )
}

/**
 * Per-call extras for the `/projects` calls. Ownership headers (a project token, an upload's
 * `x-delete-token`) go in `headers`, so adding one is a one-line change at the call site.
 */
export interface ProjectCallOptions {
  timeoutMs?: number
  headers?: Record<string, string>
}

/** A saved project plus the ownership token the server returned with it, when it returns one. */
export interface SavedProject {
  project: ProjectBundle
  token?: string
}

/** Response fields that may carry the project's ownership token (none is part of the bundle itself). */
const PROJECT_TOKEN_FIELDS = ['token', 'owner_token', 'project_token'] as const

const withoutTokens = (v: Record<string, unknown>): Record<string, unknown> => {
  const out = { ...v }
  for (const k of PROJECT_TOKEN_FIELDS) delete out[k]
  return out
}

/** `GET /projects` (1.2): multi-plan house / project bundles stored or auto-grouped by the server. */
export async function listProjects(opts: ClientOptions, call: ProjectCallOptions = {}): Promise<ProjectBundle[]> {
  const body = await request(
    opts,
    '/projects',
    { method: 'GET', headers: { ...presenterHeader(), ...call.headers } },
    call.timeoutMs ?? 15_000,
  )
  if (Array.isArray(body)) return body.filter(isProjectBundle)
  if (isObjRecord(body)) {
    if (typeof body.contract_version === 'string') checkVersion(body.contract_version, body)
    if (Array.isArray(body.projects)) return body.projects.filter(isProjectBundle)
  }
  throw new ApiError('The project list does not match the contract', 200, 'invalid_response', body)
}

/** `POST /projects` (1.2): create or update a multi-plan house / project bundle. */
export async function saveProject(
  opts: ClientOptions,
  bundle: ProjectBundle,
  call: ProjectCallOptions = {},
): Promise<SavedProject> {
  const body = await request(
    opts,
    '/projects',
    {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...presenterHeader(), ...call.headers },
      // Client-side fields (a stored token) never go into the body.
      body: JSON.stringify(withoutTokens(bundle as unknown as Record<string, unknown>)),
    },
    call.timeoutMs ?? 15_000,
  )
  if (isObjRecord(body) && typeof body.contract_version === 'string') checkVersion(body.contract_version, body)
  const outer = isObjRecord(body) ? body : {}
  const candidate = isObjRecord(body) && 'project' in body ? body.project : body
  if (!isProjectBundle(candidate))
    throw new ApiError('The saved project does not match the contract', 200, 'invalid_response', body)
  const inner = candidate as unknown as Record<string, unknown>
  const token = [...PROJECT_TOKEN_FIELDS.map((k) => outer[k]), ...PROJECT_TOKEN_FIELDS.map((k) => inner[k])].find(
    (v): v is string => typeof v === 'string' && v.length > 0,
  )
  const project = withoutTokens(inner) as unknown as ProjectBundle
  return token ? { project, token } : { project }
}

/** `DELETE /projects/:id` (1.2): ungroup/delete a server-stored project bundle. */
export async function deleteProject(opts: ClientOptions, id: string, call: ProjectCallOptions = {}): Promise<void> {
  await request(
    opts,
    `/projects/${encodeURIComponent(id)}`,
    { method: 'DELETE', headers: { ...presenterHeader(), ...call.headers } },
    call.timeoutMs ?? 15_000,
  )
}

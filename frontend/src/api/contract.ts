/**
 * AURA (Automated Understanding of Rooms & Areas) API contract: the source of truth shared by the frontend and the backend.
 * The prose version with JSON examples lives next to this file in contract.md.
 */

/** 1.1 is additive over 1.0: every new field is optional. The client accepts any major-1 body. */
export const CONTRACT_VERSION = '1.1'

export type Lang = 'en' | 'fr' | 'fi' | 'de'
export const LANGS: readonly Lang[] = ['en', 'fr', 'fi', 'de']

/** 0–100 % of the width/height of Drawing.images.rebuilt as served, origin top-left, y down. */
export type Pct = number

/** Opaque room key. For swiss-46639 it equals the synthetic room number, e.g. "3.04". */
export type RoomId = string

export interface Frame {
  dxf_min: [number, number]
  dxf_max: [number, number]
  /** The render covers the DXF extents plus this margin on every side. */
  margin_m: number
  m_per_unit: number
  y_flip: true
  /** Rendered image size in px, [width, height]. */
  px: [number, number]
}

export type Subject =
  | 'window'
  | 'door'
  | 'washbasin'
  | 'wc'
  | 'bathtub'
  | 'shower'
  | 'room'
  | 'apartment'
export const SUBJECTS: readonly Subject[] = [
  'window',
  'door',
  'washbasin',
  'wc',
  'bathtub',
  'shower',
  'room',
  'apartment',
]

export interface PctRect {
  x: Pct
  y: Pct
  w: Pct
  h: Pct
}

/** 1.1: printed-text boxes found by find_text (ids text-NN, the printed text in `note`). */
export type TextBoxSubject = 'label' | 'fire_rating'
export type BoxSubjectAny = Subject | TextBoxSubject
const BOX_SUBJECTS: readonly BoxSubjectAny[] = [...SUBJECTS, 'label', 'fire_rating']

export interface Box extends PctRect {
  /** win-01…, door-01…, basin-01…, wc-01…, text-01…; stable across calls, reading order. */
  id: string
  subject: BoxSubjectAny
  room_id?: RoomId
  unit?: string
  /** Free-text remark, e.g. a double door drawn as two leaves at the same spot. */
  note?: string
}

export interface RoomPoly {
  room_id: RoomId
  num: string
  name: string
  unit?: string
  area_m2: number
  outdoor: boolean
  points: [Pct, Pct][]
  label_at: [Pct, Pct]
  /** 1.1: 'outline' when no area is printed and it comes from the room outline. Absent means printed. */
  area_source?: 'printed' | 'outline'
  /** Present only for rooms that support what-if resizing. */
  rect?: PctRect & { w_m: number; h_m: number }
}

export type DrawingKind = 'cad_dxf' | 'raster' | 'ifc'
export const DRAWING_KINDS: readonly DrawingKind[] = ['cad_dxf', 'raster', 'ifc']

export type DrawingStatus = 'queued' | 'processing' | 'ready' | 'failed'
export const DRAWING_STATUSES: readonly DrawingStatus[] = ['queued', 'processing', 'ready', 'failed']

/** Measured gold result for a library drawing, from the eval (never estimated). */
export interface DrawingFacts {
  gold_total: number
  correct: number
  measured_at: string
  /** Dataset languages the gold rows were counted in, e.g. ["en"] (twins in other languages ask the same fact). */
  languages?: string[]
}

export interface Credit {
  text: string
  license: string
  url?: string
}

export interface Drawing {
  contract_version: string
  id: string
  title: string
  /** Optional second line, e.g. "Site 11420 · 1:200". */
  subtitle?: string
  frame: Frame
  /** Metres per 1 % of image width. */
  m_per_pct_x: number
  /** Metres per 1 % of image height. */
  m_per_pct_y: number
  images: { rebuilt: string; original?: string; original_alignment?: 'exact' | 'approximate' }
  rooms: RoomPoly[]
  units: { id: string; label: string }[]
  credit: Credit
  // 1.1 (all optional)
  kind?: DrawingKind
  status?: DrawingStatus
  /** 0–1 while processing. */
  progress?: number
  /** Human-readable reason when status is 'failed'. */
  error?: string
  /** Subjects this drawing can answer; drives suggestions and context menus. */
  capabilities?: Subject[]
  source?: 'library' | 'upload'
  /** URL of a small preview image. */
  thumbnail?: string
  /** ISO 8601. */
  created_at?: string
  /** ISO 8601; uploads expire (24 h). */
  expires_at?: string
  /** Library drawings only, from the eval. */
  facts?: DrawingFacts
}

/** One card of `GET /drawings` (1.1). */
export interface DrawingSummary {
  id: string
  title: string
  kind: DrawingKind
  status: DrawingStatus
  thumbnail: string
  facts?: DrawingFacts
  credit: Credit
  // optional extras the list may carry
  subtitle?: string
  source?: 'library' | 'upload'
  progress?: number
  error?: string
  created_at?: string
  expires_at?: string
}

/** `GET /drawings` body (1.1). */
export interface DrawingList {
  contract_version: string
  drawings: DrawingSummary[]
}

/** `GET /drawings/:id/status` body (1.1). */
export interface DrawingStatusBody {
  contract_version: string
  id: string
  status: DrawingStatus
  progress?: number
  error?: string
}

/** `POST /uploads` (multipart, field `file`) response (1.1). */
export interface UploadResult {
  contract_version?: string
  drawing_id: string
  /** Send back as `X-Delete-Token` on `DELETE /drawings/:id`. Kept in localStorage only. */
  delete_token: string
  status: 'queued'
}

export type Scope =
  | { kind: 'floor' }
  | { kind: 'unit'; unit: string }
  | { kind: 'room'; room_id: RoomId }
  | { kind: 'label'; label: string }
  | { kind: 'region'; rect: PctRect; label: string }

export interface Override {
  room_id: RoomId
  w_m: number
  h_m: number
}

export interface AskRequest {
  drawing_id: string
  question: string
  scope?: Scope
  overrides?: Override[]
  lang_hint?: Lang
}

export type TraceKind = 'exact' | 'model' | 'warn' | 'info'

export type ToolName =
  | 'count_objects'
  | 'room_areas'
  | 'list_rooms'
  | 'locate'
  | 'scope_to_region'
  | 'compare_with_original'
  | 'check_boxes_with_decisions'
  | 'what_if'
  | 'find_text'
export const TOOL_NAMES: readonly ToolName[] = [
  'count_objects',
  'room_areas',
  'list_rooms',
  'locate',
  'scope_to_region',
  'compare_with_original',
  'check_boxes_with_decisions',
  'what_if',
  'find_text',
]

/** A tool call made while answering (1.1). */
export interface ToolCall {
  name: ToolName
  args: Record<string, unknown>
  result_id: string
  ms: number
}

export interface TraceNode {
  id: string
  parent?: string
  title: string
  tag: string
  body: string
  kind: TraceKind
  evidence_ids?: string[]
  /** Probability 0–1 when numeric (0.93, not 93). */
  confidence?: number | 'sample'
  /** 1.1: present when this node is a tool call. */
  tool?: ToolCall
}

export type AbstainReason = 'not_on_drawing' | 'not_supported_yet' | 'unclear_question'

export type Answer =
  | { kind: 'count'; value: number; subject: Subject }
  | { kind: 'area'; value: number; unit: 'm2' }
  | { kind: 'abstain'; reason: AbstainReason; where_to_find?: string }
  /** 1.1: printed text found on the drawing (e.g. a fire rating), with its box in the evidence. */
  | { kind: 'text'; value: string; subject: TextSubject }

export type TextSubject = 'fire_rating' | 'label'

export interface EvidenceRoom {
  room_id: RoomId
  included: boolean
  area_m2: number
  edited?: boolean
}

export interface Check {
  method: 'decisions_api'
  status: 'sample' | 'live'
  agreed?: number
  total?: number
  /** Evidence id → probability 0–1. */
  per_box?: Record<string, number>
  /** 1.1: Decisions requests made for THIS answer's check (1 live, 0 when served from the cache). */
  calls?: number
  /** 1.1: USD of those requests (0 when cached). */
  cost_usd?: number
  /** 1.1: input tokens of those requests (0 when cached). */
  tokens?: number
  /** 1.1: true when the check came from the cache (precomputed or an earlier answer). */
  cached?: boolean
}

/** 1.1: the ranked check that can accompany an answer. */
export interface RankedCheck extends Check {
  /** The check never covers objects the geometry missed. */
  scope: 'drawn_boxes'
  /** Evidence ids, least certain first. */
  ranking: string[]
  least_certain_id?: string
  /** Highest known-negative anchor score in the same request. Agreed means p > anchors_max + 0.05. */
  anchors_max?: number
  image?: 'dataset_png' | 'upload' | 'none'
  /** Raster only, from the measured eval. */
  recall?: { value: number; n: number; source: string }
}

export type AskRoute = 'template' | 'choice' | 'agent' | 'cache'

export interface Gold {
  /** e.g. "questions.jsonl:103425" (line in the dataset file). */
  dataset_qid: string
  value: number
  tolerance: 'exact' | '2pct'
  match: boolean
}

export type Method = 'cad_geometry' | 'ocr' | 'detector' | 'vision' | 'none'

export interface AskResponse {
  contract_version: string
  answer: Answer
  evidence: { boxes: Box[]; rooms: EvidenceRoom[] }
  detected_lang: Lang
  canonical_en: string
  scope: Scope
  method: Method
  /** 1.0 bodies carry a plain Check; 1.1 bodies may add the RankedCheck fields. */
  check?: Check & Partial<Omit<RankedCheck, keyof Check>>
  /** Only when the request scope equals a dataset question's scope and there are no overrides. */
  gold?: Gold
  trace: TraceNode[]
  calls: number
  cost_usd: number
  latency_ms: number
  source: 'api' | 'local'
  // 1.1 (all optional)
  answer_id?: string
  conversation_id?: string
  turn_id?: string
  route?: AskRoute
  /** Model prose that passed the provenance filter (no number that is not in a tool result). */
  explanation?: string
}

/** `POST /chat` body (1.1); the response is `text/event-stream`. */
export interface ChatRequest {
  conversation_id?: string
  drawing_id: string
  message: string
  scope?: Scope
  overrides?: Override[]
  lang_hint?: Lang
}

/** Server-sent events of `POST /chat`, in order: turn, understood, (tool_call, tool_result)*, check?, answer | error, done. */
export type ChatEvent =
  | { event: 'turn'; data: { conversation_id: string; turn_id: string } }
  | { event: 'understood'; data: { detected_lang: Lang; canonical_en: string; route: AskRoute } }
  | { event: 'tool_call'; data: { node: TraceNode } }
  | { event: 'tool_result'; data: { node_id: string; evidence: AskResponse['evidence']; summary: string } }
  | { event: 'check'; data: { check: RankedCheck } }
  | { event: 'answer'; data: { response: AskResponse } }
  | { event: 'error'; data: { error: string; code: ErrorCode } }
  | { event: 'done'; data: Record<string, never> }
export type ChatEventName = ChatEvent['event']
export const CHAT_EVENTS: readonly ChatEventName[] = [
  'turn',
  'understood',
  'tool_call',
  'tool_result',
  'check',
  'answer',
  'error',
  'done',
]

/** `GET /conversations/:id` body (1.1). */
export interface Conversation {
  contract_version: string
  id: string
  drawing_id: string
  turns: { turn_id: string; message: string; response?: AskResponse; error?: ErrorBody }[]
}

/** `GET /budget` body (1.1). */
export interface Budget {
  contract_version: string
  models: 'on' | 'off'
  event_used: number
  event_cap: number
  day_used: number
  day_cap: number
}

/** `GET /stats` body (1.1): public counters, each with its n. */
export interface Stats {
  contract_version: string
  answers_today: number
  model_calls_today: number
  geometry_only_share?: { value: number; n: number }
  median_cost_per_checked_answer_usd?: { value: number; n: number }
}

export type ErrorCode =
  | 'bad_request'
  | 'unknown_drawing'
  | 'internal'
  // 1.1
  | 'too_large'
  | 'unsupported_type'
  | 'rate_limited'
  | 'budget_exhausted'
  | 'presenter_required'
  | 'processing'
  /** The 60 s chat-turn deadline passed (SSE `error` event; /ask answers 504). */
  | 'timeout'
export const ERROR_CODES: readonly ErrorCode[] = [
  'bad_request',
  'unknown_drawing',
  'internal',
  'too_large',
  'unsupported_type',
  'rate_limited',
  'budget_exhausted',
  'presenter_required',
  'processing',
  'timeout',
]

export interface ErrorBody {
  contract_version: string
  error: string
  code: ErrorCode
}

/** FastAPI's default validation body (422); treated as a 4xx like ErrorBody. */
export interface FastApiDetail {
  detail: unknown
}

export interface Health {
  ok: true
  contract_version: string
}

/** Evidence subjects that have boxes in the bundled drawing file. */
export type BoxSubject = 'window' | 'door' | 'washbasin'

/** One row of the dataset gold table bundled with a drawing. */
export interface GoldEntry {
  /** EN question line, e.g. "questions.jsonl:103425". */
  dataset_qid: string
  /** FR twin of the same question. */
  dataset_qid_fr: string
  /** Our English paraphrase of the dataset question (not the dataset text). */
  question_en: string
  answer: { kind: 'count'; subject: Subject } | { kind: 'area' }
  scope: Scope
  value: number
  tolerance: 'exact' | '2pct'
}

/**
 * The shared file `src/data/drawing.<id>.json`: a Drawing plus the evidence boxes and the gold table.
 * GET /drawings/:id may serve it unchanged; clients ignore the extra sections.
 */
export interface DrawingFile extends Drawing {
  evidence: Record<BoxSubject, Box[]>
  gold: GoldEntry[]
}

// ---------------------------------------------------------------------------
// Runtime guards (hand-written, no dependencies)
// ---------------------------------------------------------------------------

type Obj = Record<string, unknown>

const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const isStr = (v: unknown): v is string => typeof v === 'string'
const isOneOf = <T extends string>(v: unknown, set: readonly T[]): v is T =>
  typeof v === 'string' && (set as readonly string[]).includes(v)
const isPair = (v: unknown): v is [number, number] =>
  Array.isArray(v) && v.length === 2 && isNum(v[0]) && isNum(v[1])

const isRect = (v: unknown): v is PctRect & Obj =>
  isObj(v) && isNum(v.x) && isNum(v.y) && isNum(v.w) && isNum(v.h)

export function isScope(v: unknown): v is Scope {
  if (!isObj(v)) return false
  switch (v.kind) {
    case 'floor':
      return true
    case 'unit':
      return isStr(v.unit)
    case 'room':
      return isStr(v.room_id)
    case 'label':
      return isStr(v.label)
    case 'region':
      return isRect(v.rect) && isStr(v.label)
    default:
      return false
  }
}

export function isBox(v: unknown): v is Box {
  return (
    isObj(v) &&
    isRect(v) &&
    isStr(v.id) &&
    isOneOf(v.subject, BOX_SUBJECTS) &&
    (v.room_id === undefined || isStr(v.room_id))
  )
}

function isAnswer(v: unknown): v is Answer {
  if (!isObj(v)) return false
  if (v.kind === 'count') return isNum(v.value) && isOneOf(v.subject, SUBJECTS)
  if (v.kind === 'area') return isNum(v.value) && v.unit === 'm2'
  if (v.kind === 'text') return isStr(v.value) && isOneOf(v.subject, ['fire_rating', 'label'] as const)
  if (v.kind === 'abstain')
    return (
      isOneOf(v.reason, ['not_on_drawing', 'not_supported_yet', 'unclear_question'] as const) &&
      (v.where_to_find === undefined || isStr(v.where_to_find))
    )
  return false
}

function isTraceNode(v: unknown): v is TraceNode {
  return (
    isObj(v) &&
    isStr(v.id) &&
    isStr(v.title) &&
    isStr(v.tag) &&
    isStr(v.body) &&
    isOneOf(v.kind, ['exact', 'model', 'warn', 'info'] as const) &&
    (v.parent === undefined || isStr(v.parent)) &&
    (v.evidence_ids === undefined || (Array.isArray(v.evidence_ids) && v.evidence_ids.every(isStr))) &&
    (v.confidence === undefined || v.confidence === 'sample' || isNum(v.confidence)) &&
    (v.tool === undefined || isToolCall(v.tool))
  )
}

function isToolCall(v: unknown): v is ToolCall {
  return isObj(v) && isOneOf(v.name, TOOL_NAMES) && isObj(v.args) && isStr(v.result_id) && isNum(v.ms)
}

const optStr = (v: unknown) => v === undefined || isStr(v)
const optNum = (v: unknown) => v === undefined || isNum(v)

function isCheck(v: unknown): boolean {
  if (!isObj(v) || v.method !== 'decisions_api' || !isOneOf(v.status, ['sample', 'live'] as const)) return false
  return (
    optNum(v.agreed) &&
    optNum(v.total) &&
    (v.per_box === undefined || (isObj(v.per_box) && Object.values(v.per_box).every(isNum))) &&
    optNum(v.calls) &&
    optNum(v.cost_usd) &&
    optNum(v.tokens) &&
    (v.cached === undefined || typeof v.cached === 'boolean') &&
    (v.scope === undefined || v.scope === 'drawn_boxes') &&
    (v.ranking === undefined || (Array.isArray(v.ranking) && v.ranking.every(isStr))) &&
    optStr(v.least_certain_id) &&
    optNum(v.anchors_max) &&
    (v.image === undefined || isOneOf(v.image, ['dataset_png', 'upload', 'none'] as const)) &&
    (v.recall === undefined || (isObj(v.recall) && isNum(v.recall.value) && isNum(v.recall.n) && isStr(v.recall.source)))
  )
}

/** A 1.1 RankedCheck as carried by the `check` chat event. */
export function isRankedCheck(v: unknown): v is RankedCheck {
  return isCheck(v) && isObj(v) && v.scope === 'drawn_boxes' && Array.isArray(v.ranking)
}

export function isAskResponse(v: unknown): v is AskResponse {
  if (!isObj(v)) return false
  const ev = v.evidence
  if (!isObj(ev) || !Array.isArray(ev.boxes) || !Array.isArray(ev.rooms)) return false
  if (!ev.boxes.every(isBox)) return false
  if (!ev.rooms.every((r) => isObj(r) && isStr(r.room_id) && typeof r.included === 'boolean' && isNum(r.area_m2)))
    return false
  if (v.gold !== undefined) {
    const g = v.gold
    if (!isObj(g) || !isStr(g.dataset_qid) || !isNum(g.value) || typeof g.match !== 'boolean') return false
    if (!isOneOf(g.tolerance, ['exact', '2pct'] as const)) return false
  }
  if (v.check !== undefined && !isCheck(v.check)) return false
  return (
    isStr(v.contract_version) &&
    isAnswer(v.answer) &&
    isOneOf(v.detected_lang, LANGS) &&
    isStr(v.canonical_en) &&
    isScope(v.scope) &&
    isOneOf(v.method, ['cad_geometry', 'ocr', 'detector', 'vision', 'none'] as const) &&
    Array.isArray(v.trace) &&
    v.trace.every(isTraceNode) &&
    isNum(v.calls) &&
    isNum(v.cost_usd) &&
    isNum(v.latency_ms) &&
    isOneOf(v.source, ['api', 'local'] as const) &&
    optStr(v.answer_id) &&
    optStr(v.conversation_id) &&
    optStr(v.turn_id) &&
    (v.route === undefined || isOneOf(v.route, ['template', 'choice', 'agent', 'cache'] as const)) &&
    optStr(v.explanation)
  )
}

function isRoomPoly(v: unknown): v is RoomPoly {
  return (
    isObj(v) &&
    isStr(v.room_id) &&
    isStr(v.num) &&
    isStr(v.name) &&
    isNum(v.area_m2) &&
    typeof v.outdoor === 'boolean' &&
    Array.isArray(v.points) &&
    v.points.every(isPair) &&
    isPair(v.label_at) &&
    (v.area_source === undefined || isOneOf(v.area_source, ['printed', 'outline'] as const)) &&
    (v.rect === undefined || (isRect(v.rect) && isNum(v.rect.w_m) && isNum(v.rect.h_m)))
  )
}

/** Accepts a Drawing; extra sections (e.g. `evidence`, `gold` of DrawingFile) are tolerated. */
export function isDrawing(v: unknown): v is Drawing {
  if (!isObj(v)) return false
  const f = v.frame
  const img = v.images
  const credit = v.credit
  return (
    isStr(v.contract_version) &&
    isStr(v.id) &&
    isStr(v.title) &&
    isObj(f) &&
    isPair(f.dxf_min) &&
    isPair(f.dxf_max) &&
    isNum(f.margin_m) &&
    isNum(f.m_per_unit) &&
    f.y_flip === true &&
    isPair(f.px) &&
    isNum(v.m_per_pct_x) &&
    isNum(v.m_per_pct_y) &&
    isObj(img) &&
    isStr(img.rebuilt) &&
    Array.isArray(v.rooms) &&
    v.rooms.every(isRoomPoly) &&
    Array.isArray(v.units) &&
    v.units.every((u) => isObj(u) && isStr(u.id) && isStr(u.label)) &&
    isCredit(credit) &&
    (v.kind === undefined || isOneOf(v.kind, DRAWING_KINDS)) &&
    (v.status === undefined || isOneOf(v.status, DRAWING_STATUSES)) &&
    optNum(v.progress) &&
    optStr(v.error) &&
    (v.capabilities === undefined || (Array.isArray(v.capabilities) && v.capabilities.every((c) => isOneOf(c, SUBJECTS)))) &&
    (v.source === undefined || isOneOf(v.source, ['library', 'upload'] as const)) &&
    optStr(v.thumbnail) &&
    optStr(v.created_at) &&
    optStr(v.expires_at) &&
    (v.facts === undefined || isFacts(v.facts))
  )
}

function isCredit(v: unknown): v is Credit {
  return isObj(v) && isStr(v.text) && isStr(v.license) && optStr(v.url)
}

function isFacts(v: unknown): v is DrawingFacts {
  return (
    isObj(v) &&
    isNum(v.gold_total) &&
    isNum(v.correct) &&
    isStr(v.measured_at) &&
    (v.languages === undefined || (Array.isArray(v.languages) && v.languages.every(isStr)))
  )
}

export function isDrawingSummary(v: unknown): v is DrawingSummary {
  return (
    isObj(v) &&
    isStr(v.id) &&
    isStr(v.title) &&
    isOneOf(v.kind, DRAWING_KINDS) &&
    isOneOf(v.status, DRAWING_STATUSES) &&
    isStr(v.thumbnail) &&
    (v.facts === undefined || isFacts(v.facts)) &&
    isCredit(v.credit) &&
    optStr(v.subtitle) &&
    (v.source === undefined || isOneOf(v.source, ['library', 'upload'] as const)) &&
    optNum(v.progress) &&
    optStr(v.error)
  )
}

export function isDrawingList(v: unknown): v is DrawingList {
  return isObj(v) && isStr(v.contract_version) && Array.isArray(v.drawings) && v.drawings.every(isDrawingSummary)
}

export function isDrawingStatusBody(v: unknown): v is DrawingStatusBody {
  return (
    isObj(v) &&
    isStr(v.contract_version) &&
    isStr(v.id) &&
    isOneOf(v.status, DRAWING_STATUSES) &&
    optNum(v.progress) &&
    optStr(v.error)
  )
}

export function isUploadResult(v: unknown): v is UploadResult {
  return (
    isObj(v) &&
    optStr(v.contract_version) &&
    isStr(v.drawing_id) &&
    isStr(v.delete_token) &&
    v.status === 'queued'
  )
}

export function isDrawingFile(v: unknown): v is DrawingFile {
  if (!isDrawing(v)) return false
  const o = v as unknown as Obj
  const ev = o.evidence
  return (
    isObj(ev) &&
    (['window', 'door', 'washbasin'] as const).every((k) => Array.isArray(ev[k]) && (ev[k] as unknown[]).every(isBox)) &&
    Array.isArray(o.gold)
  )
}

export function isErrorBody(v: unknown): v is ErrorBody {
  return (
    isObj(v) &&
    isStr(v.contract_version) &&
    isStr(v.error) &&
    isOneOf(v.code, ERROR_CODES)
  )
}

export function isFastApiDetail(v: unknown): v is FastApiDetail {
  return isObj(v) && 'detail' in v
}

export function isHealth(v: unknown): v is Health {
  return isObj(v) && v.ok === true && isStr(v.contract_version)
}

/** Major part of a "major.minor" version string. */
export function majorOf(version: string): string {
  return version.split('.')[0] ?? ''
}

/**
 * AURA (Automated Understanding of Rooms & Areas) API contract: the source of truth shared by the frontend and the backend.
 * The prose version with JSON examples lives next to this file in contract.md.
 */

export const CONTRACT_VERSION = '1.0'

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

export interface Box extends PctRect {
  /** win-01…, door-01…, basin-01…, wc-01…; stable across calls, reading order. */
  id: string
  subject: Subject
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
  /** Present only for rooms that support what-if resizing. */
  rect?: PctRect & { w_m: number; h_m: number }
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
  credit: { text: string; license: string; url?: string }
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
}

export type AbstainReason = 'not_on_drawing' | 'not_supported_yet' | 'unclear_question'

export type Answer =
  | { kind: 'count'; value: number; subject: Subject }
  | { kind: 'area'; value: number; unit: 'm2' }
  | { kind: 'abstain'; reason: AbstainReason; where_to_find?: string }

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
}

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
  check?: Check
  /** Only when the request scope equals a dataset question's scope and there are no overrides. */
  gold?: Gold
  trace: TraceNode[]
  calls: number
  cost_usd: number
  latency_ms: number
  source: 'api' | 'local'
}

export type ErrorCode = 'bad_request' | 'unknown_drawing' | 'internal'

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
    isOneOf(v.subject, SUBJECTS) &&
    (v.room_id === undefined || isStr(v.room_id))
  )
}

function isAnswer(v: unknown): v is Answer {
  if (!isObj(v)) return false
  if (v.kind === 'count') return isNum(v.value) && isOneOf(v.subject, SUBJECTS)
  if (v.kind === 'area') return isNum(v.value) && v.unit === 'm2'
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
    (v.confidence === undefined || v.confidence === 'sample' || isNum(v.confidence))
  )
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
  if (v.check !== undefined) {
    const c = v.check
    if (!isObj(c) || c.method !== 'decisions_api' || !isOneOf(c.status, ['sample', 'live'] as const)) return false
  }
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
    isOneOf(v.source, ['api', 'local'] as const)
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
    isObj(credit) &&
    isStr(credit.text) &&
    isStr(credit.license)
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
    isOneOf(v.code, ['bad_request', 'unknown_drawing', 'internal'] as const)
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

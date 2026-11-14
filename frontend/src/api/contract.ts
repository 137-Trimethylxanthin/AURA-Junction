/**
 * AURA (Automated Understanding of Rooms & Areas) API contract: the source of truth shared by the frontend and the backend.
 * The prose version with JSON examples lives next to this file in contract.md.
 */

/** 1.2 is additive over 1.0/1.1: every new field is optional. The client accepts any major-1 body. */
export const CONTRACT_VERSION = '1.2'

export type Lang = 'en' | 'fr' | 'fi' | 'de'
export const LANGS: readonly Lang[] = ['en', 'fr', 'fi', 'de']

/** 1.2: 6 WCAG AA categorical evidence series tokens (`--group-1..6` + `--group-1..6-surface` in STACK.md / index.css). */
export type GroupColor = 'c1' | 'c2' | 'c3' | 'c4' | 'c5' | 'c6'
export const GROUP_COLORS: readonly GroupColor[] = ['c1', 'c2', 'c3', 'c4', 'c5', 'c6']

export function groupColorVars(color: GroupColor): { fg: string; surface: string; index: 1 | 2 | 3 | 4 | 5 | 6 } {
  const idx = (GROUP_COLORS.indexOf(color) + 1) as 1 | 2 | 3 | 4 | 5 | 6
  const safeIdx = idx >= 1 && idx <= 6 ? idx : 1
  return {
    fg: `var(--group-${safeIdx})`,
    surface: `var(--group-${safeIdx}-surface)`,
    index: safeIdx,
  }
}

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
  | 'bedroom'
  | 'kitchen'
  | 'bathroom'
  | 'wc_room'
  | 'sauna'
  | 'sink'
  | 'shower_or_bathtub'
  | 'storey'
  | 'balcony'
export const SUBJECTS: readonly Subject[] = [
  'window',
  'door',
  'washbasin',
  'wc',
  'bathtub',
  'shower',
  'room',
  'apartment',
  'bedroom',
  'kitchen',
  'bathroom',
  'wc_room',
  'sauna',
  'sink',
  'shower_or_bathtub',
  'storey',
  'balcony',
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
export const BOX_SUBJECT_NAMES: readonly BoxSubjectAny[] = BOX_SUBJECTS

export interface Box extends PctRect {
  /** win-01…, door-01…, basin-01…, wc-01…, text-01…; stable across calls, reading order. */
  id: string
  subject: BoxSubjectAny
  room_id?: RoomId
  unit?: string
  /** Free-text remark from the solver (e.g. an IFC element id). Shown verbatim as a neutral line; never interpreted. */
  note?: string
  /**
   * 1.1: this box is the same object drawn a second time at the same spot in the CAD file; names the first copy.
   * Both are counted, as the dataset does. This field (not `note`) is the only signal for "drawn twice".
   */
  duplicate_of?: string
  /** 1.2: categorical color-group id referencing `Evidence.groups[].id`. */
  group_id?: string
  /** 1.2: floor drawing id when evidence spans a multi-plan house/project bundle. */
  drawing_id?: string
}

export interface RoomPoly {
  room_id: RoomId
  num: string
  name: string
  unit?: string
  /** null: no area printed and none measurable from an outline. Never 0 for unknown (1.1). */
  area_m2: number | null
  outdoor: boolean
  points: [Pct, Pct][]
  label_at: [Pct, Pct]
  /** 1.1: 'outline' when no area is printed and it comes from the room outline. Absent means printed. */
  area_source?: 'printed' | 'outline' | 'estimate'
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
  /** Licence id (SPDX or LicenseRef-…): for machines, never shown raw when `license_name` exists. */
  license: string
  /** 1.1: the licence as people read it, e.g. "KIT IFC Examples: for unrestricted use". */
  license_name?: string
  url?: string
}

/** The licence text to show: the human name, else an SPDX id such as "CC BY 4.0", never a LicenseRef-… id. */
/**
 * Links and image URLs that come from the API or a drawing file: only http(s) and root-relative paths pass;
 * anything else (javascript:, data:, vbscript:, protocol-relative //host) gives null and is not rendered.
 */
export function safeUrl(u: string | undefined): string | null {
  if (!u) return null
  const s = u.trim()
  if (s.startsWith('/') && !s.startsWith('//')) return s
  return /^https?:\/\//i.test(s) ? s : null
}

export function licenceLabel(c: Pick<Credit, 'license' | 'license_name'>): string {
  if (c.license_name) return c.license_name
  return /^LicenseRef-/i.test(c.license) ? 'Licence terms' : c.license
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
  /** 1.2: original uploaded file extension (e.g. ".pdf" when kind === "raster"). */
  source_ext?: string
  /** 1.2: parent house/building project bundle id when grouped. */
  project_id?: string
  /** 1.2: short floor badge label (e.g. "KG", "EG", "1. OG", "GF", "Page 2"). */
  floor_label?: string
  /** 1.2: vertical floor sort order (e.g. -1 basement, 0 ground, 1..N upper, 99 attic). */
  floor_order?: number
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
  /** 1.2: original uploaded file extension (e.g. ".pdf" when kind === "raster"). */
  source_ext?: string
  /** 1.2: parent house/building project bundle id when grouped. */
  project_id?: string
  /** 1.2: short floor badge label (e.g. "KG", "EG", "1. OG", "GF", "Page 2"). */
  floor_label?: string
  /** 1.2: vertical floor sort order (e.g. -1 basement, 0 ground, 1..N upper, 99 attic). */
  floor_order?: number
}

/** 1.2: one ordered floor entry inside a multi-plan house/building `ProjectBundle`. */
export interface ProjectFloor {
  drawing_id: string
  label: string
  order: number
}

/** 1.2: grouped house/building project bundle enabling fast floor switching & cross-plan matching. */
export interface ProjectBundle {
  id: string
  title: string
  floors: ProjectFloor[]
  auto_matched?: boolean
  created_at?: string
}

/** 1.2: `GET /projects` body. */
export interface ProjectList {
  contract_version: string
  projects: ProjectBundle[]
}

/** `GET /drawings` body (1.1). */
export interface DrawingList {
  contract_version: string
  drawings: DrawingSummary[]
  /** Client-only, never sent: rows left out because they failed validation (salvageDrawingList). */
  dropped?: number
}

/**
 * Fine-grained upload stage (store ingest). `status` keeps its four values; `stage` says where inside them a file is.
 * Unknown future stages are tolerated (the upload page shows them as "working").
 */
export type UploadStage =
  | 'queued'
  | 'parsing'
  | 'reading'
  | 'interpreting'
  | 'materialising'
  | 'indexed'
  | 'indexed_ours'
  | 'indexed_store'
  | 'ready_unindexed'
  | 'partial'
  | 'store_failed'
  | 'failed'

/** Counts the store or our parse found so far; each absent until reported. */
export interface UploadCounts {
  pages?: number
  rooms?: number
  apartments?: number
  doors?: number
  windows?: number
  fixtures?: number
  texts?: number
}

/** How each subject was found: "vision" is a model estimate; the others are read from the file. */
export type UploadMethods = Partial<Record<'rooms' | 'doors' | 'windows' | 'fixtures', 'cad_geometry' | 'pdf_vector' | 'ocr' | 'vision' | string>>

/** One page of a multi-page upload (a PDF page is read separately). */
export interface UploadPage {
  /** 1-based page number. */
  page?: number
  /** Floor label when one was found (e.g. "EG"). */
  label?: string
  file_id?: string
  stage?: string
  /** 0-1, only when the backend measures it. */
  progress?: number
  counts?: UploadCounts
  methods?: UploadMethods
  error?: string
  /** The drawing made from this page, once there is one. */
  drawing_id?: string
}

/** `GET /drawings/:id/status` body (1.1; the store fields are optional extras the page tolerates missing). */
export interface DrawingStatusBody {
  contract_version: string
  id: string
  status: DrawingStatus
  progress?: number
  error?: string
  stage?: string
  pages?: UploadPage[]
  counts?: UploadCounts
  /** How each subject was found (per page in pages[].methods). */
  methods?: UploadMethods
  /** Subjects whose count is a model estimate (e.g. ["doors", "windows"]). */
  estimates?: string[]
  mode?: 'full' | 'quick'
  /** A short reason or remark from the server (e.g. why the store failed). */
  note?: string
  ingest_calls?: number
  /** Absent when the token usage is unknown. */
  ingest_cost_usd?: number
  /** Seconds since the upload was accepted. */
  elapsed_s?: number
  /** Seconds in the current stage. */
  stage_s?: number
}

/** One accepted upload type of `GET /uploads/accepted`. */
export interface AcceptedType {
  type: string
  ext: string[]
  max_mb: number
  presenter_only?: boolean
}

/** `GET /uploads/accepted`: what this server takes, in its current mode. */
export interface AcceptedUploads {
  contract_version?: string
  mode?: 'full' | 'quick'
  types: AcceptedType[]
  ttl_hours?: number
}

export function isAcceptedUploads(v: unknown): v is AcceptedUploads {
  if (typeof v !== 'object' || v === null || !Array.isArray((v as AcceptedUploads).types)) return false
  return (v as AcceptedUploads).types.every(
    (t) =>
      typeof t === 'object' && t !== null && typeof t.type === 'string' && Array.isArray(t.ext) &&
      t.ext.every((e) => typeof e === 'string') && typeof t.max_mb === 'number' && Number.isFinite(t.max_mb),
  )
}

/** `POST /uploads` (multipart, field `file`) response (1.1). */
export interface UploadResult {
  contract_version?: string
  drawing_id: string
  /** Send back as `X-Delete-Token` on `DELETE /drawings/:id`. Kept in localStorage only. */
  delete_token: string
  status: 'queued'
}

export interface ScopeProject {
  kind: 'project'
  project_id: string
  drawing_ids: string[]
}

export type Scope =
  | { kind: 'floor' }
  /** 1.1: the whole building model (IFC), not just the drawing's storey. */
  | { kind: 'model' }
  | { kind: 'unit'; unit: string }
  | { kind: 'room'; room_id: RoomId }
  | { kind: 'label'; label: string }
  | { kind: 'region'; rect: PctRect; label: string }
  /** 1.2: cross-plan house/project bundle scope across multiple floor drawings. */
  | ScopeProject

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
  /**
   * 1.2: a sheet of a multi-plan building is answered for the WHOLE building unless the question names a floor
   * ("this floor", "ground floor", "level 2") or `sheet_ids` selects some sheets (default: all sheets).
   */
  project_id?: string
  sheet_ids?: string[]
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
  | 'deep_agent'
  | 'sql'
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
  'deep_agent',
  'sql',
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
  /** Tool-step answerer stage; `detail` has one shape per stage (read defensively, see features/trace/detail.ts). */
  stage?: 'resources' | 'decision' | 'filter' | 'answer' | 'scope'
  detail?: Record<string, unknown>
}

export type AbstainReason =
  | 'not_on_drawing'
  | 'not_supported_yet'
  | 'unclear_question'
  | 'low_confidence'
  | 'not_answerable'

/** 1.2: open-ended answer from the deep agent (unmapped unit, text/comparison, or open subject). */
export interface AnswerNote {
  kind: 'note'
  value: string
  number?: number
  unit?: string
}

export type Answer =
  | { kind: 'count'; value: number; subject: Subject }
  | { kind: 'area'; value: number; unit: 'm2'; approximate?: boolean }
  | { kind: 'abstain'; reason: AbstainReason; where_to_find?: string }
  /** 1.1: a measured quantity given as is (IFC wall length, net side area, net volume); never recomputed. */
  | { kind: 'quantity'; value: number; unit: 'm' | 'm2' | 'm3' }
  /** 1.1: printed text found on the drawing (e.g. a fire rating), with its box in the evidence. */
  | { kind: 'text'; value: string; subject: TextSubject }
  | AnswerNote

export type TextSubject = 'fire_rating' | 'label'

export interface EvidenceRow {
  id: string
  name?: string
  value?: number
}

export interface EvidenceRoom {
  room_id: RoomId
  /** Always false when `area_m2` is null: a room with no known area is never summed. */
  included: boolean
  /** null: the area is not printed (1.1). Never 0 for unknown. */
  area_m2: number | null
  edited?: boolean
  /** 1.2: categorical color-group id referencing `Evidence.groups[].id`. */
  group_id?: string
  /** 1.2: floor drawing id when evidence spans a multi-plan house/project bundle. */
  drawing_id?: string
}

/** 1.2: categorical color-coded evidence group (e.g. Apartment A, Stair S1 -> 1. OG). */
export interface EvidenceGroup {
  id: string
  label: string
  color: GroupColor
  target_drawing_id?: string
}

/** 1.2: directional vector on a plan (e.g. stair/ramp/circulation lead-to route). */
export interface EvidenceLink {
  id: string
  from_pos: [Pct, Pct]
  to_pos: [Pct, Pct]
  waypoints?: [Pct, Pct][]
  group_id?: string
  label?: string
  drawing_id?: string
  target_drawing_id?: string
}

export interface Evidence {
  boxes: Box[]
  rooms: EvidenceRoom[]
  /** 1.1: counted elements without a 2D box (IFC walls, columns, whole-model counts); id = IFC GlobalId. */
  rows?: EvidenceRow[]
  /** 1.2: categorical color groups referenced by `group_id` on boxes, rooms, and links. */
  groups?: EvidenceGroup[]
  /** 1.2: directional flow vectors (e.g. stairs and where they lead). */
  links?: EvidenceLink[]
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

export type Method =
  | 'cad_geometry'
  | 'ocr'
  | 'detector'
  | 'vision'
  | 'agent_estimate'
  | 'pdf_vector'
  | 'store_sql'
  | 'none'
export const METHODS: readonly Method[] = [
  'cad_geometry',
  'ocr',
  'detector',
  'vision',
  'agent_estimate',
  'pdf_vector',
  'store_sql',
  'none',
]

export interface AskResponse {
  contract_version: string
  /** Client-only, never sent: evidence or trace items dropped because they failed validation (see salvageAskResponse). */
  dropped_items?: number
  answer: Answer
  /** `rows` (1.1): counted elements without a 2D box; `groups`/`links` (1.2): categorical colors & flow vectors. */
  evidence: Evidence
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
  /**
   * Raster drawings (a model estimate, not geometry). Confident (>= 0.6): a count with method 'vision', no boxes, and
   * `measured` = how often such answers were right. Below 0.6: abstain 'low_confidence' with `probabilities`.
   */
  estimate?: { confidence: number; probabilities?: Record<string, number>; measured?: { correct: number; n: number; source: string } }
  /** Set only on the abstain of a failed deep-agent run: service down, deadline, or the run returned no answer. */
  agent_failure?: AgentFailure
  /** 1.2: per-sheet shares of a whole-building answer (`scope` is then the project scope of the sheets asked). */
  breakdown?: BreakdownItem[]
  /** 1.2: set only when the question asked for a non-default unit ("in square feet"); `answer` stays metric. */
  display?: Display
}

/** The answer in the unit the question asked for; `factors` are display units per m / m2 / m3. */
export interface Display {
  unit: string
  label: string
  style?: 'decimal' | 'ft_in'
  factors: Partial<Record<'m' | 'm2' | 'm3', number>>
  labels: Partial<Record<'m' | 'm2' | 'm3', string>>
  value?: number
  /** "3,675 sq ft" */
  text?: string
  /** "341.42 m²" */
  metric_text?: string
  metric_explanation?: string
  metric_note?: string
}

/** Guard: a malformed `display` is ignored (the metric answer is shown), never a failed response. */
export function usableDisplay(v: unknown): Display | undefined {
  if (!isObj(v) || !isStr(v.unit) || !isStr(v.label) || !isObj(v.factors) || !isObj(v.labels)) return undefined
  if (!Object.values(v.factors).every((f) => isNum(f) && f > 0)) return undefined
  if (!Object.values(v.labels).every(isStr)) return undefined
  return v as unknown as Display
}

/** One sheet's share of a whole-building answer ("ground floor 14"); evidence items carry their sheet in `drawing_id`. */
export interface BreakdownItem {
  drawing_id: string
  label: string
  status: 'ok' | 'abstain' | 'error'
  /** The sheet's count / area / quantity when it is summable. */
  value?: number
  unit?: string
}

export type AgentFailure = 'unavailable' | 'timeout' | 'error'

/** `POST /chat` body (1.1); the response is `text/event-stream`. */
export interface ChatRequest {
  conversation_id?: string
  drawing_id: string
  message: string
  scope?: Scope
  overrides?: Override[]
  lang_hint?: Lang
  /** See `AskRequest`. */
  project_id?: string
  sheet_ids?: string[]
}

/** Server-sent events of `POST /chat`, in order: turn, progress*, understood, (tool_call, tool_result)*, check?, answer | error, done. */
export type ChatEvent =
  | { event: 'turn'; data: { conversation_id: string; turn_id: string } }
  | { event: 'progress'; data: { queue_position: number } }
  | { event: 'understood'; data: { detected_lang: Lang; canonical_en: string; route: AskRoute } }
  | { event: 'tool_call'; data: { node: TraceNode } }
  | { event: 'tool_result'; data: { node_id: string; evidence: AskResponse['evidence']; summary: string } }
  | { event: 'check'; data: { check: Check & Partial<Omit<RankedCheck, keyof Check>> } }
  | { event: 'answer'; data: { response: AskResponse } }
  | { event: 'error'; data: { error: string; code: ErrorCode; calls?: number; cost_usd?: number } }
  | { event: 'done'; data: Record<string, never> }
export type ChatEventName = ChatEvent['event']
export const CHAT_EVENTS: readonly ChatEventName[] = [
  'turn',
  'progress',
  'understood',
  'tool_call',
  'tool_result',
  'check',
  'answer',
  'error',
  'done',
]

/** `POST /rewrite` body (1.1): a rambling message or voice transcript to split into questions. */
export interface RewriteRequest {
  drawing_id: string
  /** ≤ 2000 characters. */
  text: string
}

/** One proposed question; confirm it by sending `question` with this exact `scope` to /ask or /chat. */
export interface RewriteBlock {
  question: string
  intent: 'count' | 'area' | 'other'
  subject?: string
  scope: Scope
  scope_exists: boolean
  /** false: the scope is not on this drawing or the router cannot place it (shown greyed out). */
  routable: boolean
}

/** `POST /rewrite` response (1.1). One flash-lite call: show "1 model call · $x" on the rewrite step. */
export interface RewriteResponse {
  contract_version: string
  drawing_id: string
  detected_lang: Lang
  blocks: RewriteBlock[]
  /** Parts of the text that this drawing cannot answer, in the user's words. */
  not_answerable: string[]
  calls: number
  cost_usd: number
  tokens: number
  model: string
}

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
  /** Running totals across all answers; never shown with a cap. */
  model_calls_total?: number
  cost_usd_total?: number
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
  /** 404 for an unknown conversation or answer (`/conversations/:id`, `/answers/:id/evidence.csv`). */
  | 'not_found'
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
  'not_found',
]

export interface ErrorBody {
  contract_version: string
  error: string
  code: ErrorCode
  /** 504 (timeout) only: model requests already sent for this turn, and their cost. Counted in the session total. */
  calls?: number
  cost_usd?: number
}

/** FastAPI's default validation body (422); treated as a 4xx like ErrorBody. */
export interface FastApiDetail {
  detail: unknown
}

export type AnswererMode = 'hybrid' | 'agent' | 'exact'
export const ANSWERER_MODES: readonly AnswererMode[] = ['hybrid', 'agent', 'exact']

/** 200 with ok:true when the server can answer; 503 with ok:false (same body) when the database or data dir is down. */
export interface Health {
  ok: true
  contract_version: string
  /** Diagnostics, informational only (not used by the client): db, data_dir, drawings, drawings_skipped, models, budget_left. */
  checks?: Record<string, unknown>
  /** 1.2: active server answerer mode ('hybrid' | 'agent' | 'exact'). */
  answerer?: AnswererMode
  /** 1.2: whether the deep-agent sidecar responded healthy on its last probe. */
  agent_ok?: boolean
  /** Drawing store (Postgres) availability: "on" = full mode, "off" = quick mode. */
  store?: 'on' | 'off' | boolean
}

/** Evidence subjects that have boxes in the bundled drawing file (wc/bathtub/shower since the regenerated file). */
export type BoxSubject = 'window' | 'door' | 'washbasin' | 'wc' | 'bathtub' | 'shower'
/** Layers every drawing file carries; the fixture layers are optional (older files lack them). */
export type RequiredBoxSubject = 'window' | 'door' | 'washbasin'

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
  evidence: Record<RequiredBoxSubject, Box[]> & Partial<Record<Exclude<BoxSubject, RequiredBoxSubject>, Box[]>>
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
const isPct = (v: unknown): v is Pct => isNum(v) && v >= 0 && v <= 100
const isPctPair = (v: unknown): v is [Pct, Pct] =>
  Array.isArray(v) && v.length === 2 && isPct(v[0]) && isPct(v[1])

const isRect = (v: unknown): v is PctRect & Obj =>
  isObj(v) && isNum(v.x) && isNum(v.y) && isNum(v.w) && isNum(v.h)

export function isScope(v: unknown): v is Scope {
  if (!isObj(v)) return false
  switch (v.kind) {
    case 'floor':
    case 'model':
      return true
    case 'unit':
      return isStr(v.unit)
    case 'room':
      return isStr(v.room_id)
    case 'label':
      return isStr(v.label)
    case 'region':
      return isRect(v.rect) && isStr(v.label)
    case 'project':
      return (
        isStr(v.project_id) &&
        v.project_id.length > 0 &&
        Array.isArray(v.drawing_ids) &&
        v.drawing_ids.length > 0 &&
        v.drawing_ids.every((id) => isStr(id) && id.length > 0)
      )
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
    (v.room_id === undefined || isStr(v.room_id)) &&
    (v.unit === undefined || isStr(v.unit)) &&
    (v.note === undefined || isStr(v.note)) &&
    (v.duplicate_of === undefined || isStr(v.duplicate_of)) &&
    (v.group_id === undefined || isStr(v.group_id)) &&
    (v.drawing_id === undefined || isStr(v.drawing_id))
  )
}

export function isEvidenceGroup(v: unknown): v is EvidenceGroup {
  return (
    isObj(v) &&
    isStr(v.id) &&
    v.id.length > 0 &&
    isStr(v.label) &&
    v.label.length > 0 &&
    isOneOf(v.color, GROUP_COLORS) &&
    (v.target_drawing_id === undefined || isStr(v.target_drawing_id))
  )
}

export function isEvidenceLink(v: unknown): v is EvidenceLink {
  return (
    isObj(v) &&
    isStr(v.id) &&
    v.id.length > 0 &&
    isPctPair(v.from_pos) &&
    isPctPair(v.to_pos) &&
    (v.waypoints === undefined || (Array.isArray(v.waypoints) && v.waypoints.every(isPctPair))) &&
    (v.group_id === undefined || isStr(v.group_id)) &&
    (v.label === undefined || isStr(v.label)) &&
    (v.drawing_id === undefined || isStr(v.drawing_id)) &&
    (v.target_drawing_id === undefined || isStr(v.target_drawing_id))
  )
}

function isAnswer(v: unknown): v is Answer {
  if (!isObj(v)) return false
  if (v.kind === 'count') return isNum(v.value) && isOneOf(v.subject, SUBJECTS)
  if (v.kind === 'area') return isNum(v.value) && v.unit === 'm2'
  if (v.kind === 'quantity') return isNum(v.value) && isOneOf(v.unit, ['m', 'm2', 'm3'] as const)
  if (v.kind === 'text') return isStr(v.value) && isOneOf(v.subject, ['fire_rating', 'label'] as const)
  if (v.kind === 'note') return isStr(v.value) && optNum(v.number) && optStr(v.unit)
  if (v.kind === 'abstain')
    return (
      isOneOf(v.reason, ['not_on_drawing', 'not_supported_yet', 'unclear_question', 'low_confidence', 'not_answerable'] as const) &&
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
    (v.tool === undefined || isToolCall(v.tool)) &&
    (v.stage === undefined || isStr(v.stage)) &&
    (v.detail === undefined || isObj(v.detail))
  )
}

function isToolCall(v: unknown): v is ToolCall {
  return isObj(v) && isOneOf(v.name, TOOL_NAMES) && isObj(v.args) && isStr(v.result_id) && isNum(v.ms)
}

const optStr = (v: unknown) => v === undefined || isStr(v)
const optNum = (v: unknown) => v === undefined || isNum(v)

/** Any 1.0/1.1 check body (ranked or not); a `check` chat event may carry a plain sample check. */
export function isCheck(v: unknown): v is Check & Partial<Omit<RankedCheck, keyof Check>> {
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
  if (!ev.rooms.every(evRoomOk)) return false
  if (ev.rows !== undefined && !(Array.isArray(ev.rows) && ev.rows.every(evRowOk))) return false
  if (ev.groups !== undefined && !(Array.isArray(ev.groups) && ev.groups.every(isEvidenceGroup))) return false
  if (ev.links !== undefined && !(Array.isArray(ev.links) && ev.links.every(isEvidenceLink))) return false
  if (v.dropped_items !== undefined && !isNum(v.dropped_items)) return false
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
    isOneOf(v.method, METHODS) &&
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
    optStr(v.explanation) &&
    (v.agent_failure === undefined || isOneOf(v.agent_failure, ['unavailable', 'timeout', 'error'] as const))
  )
}

function isRoomPoly(v: unknown): v is RoomPoly {
  return (
    isObj(v) &&
    isStr(v.room_id) &&
    isStr(v.num) &&
    isStr(v.name) &&
    (v.area_m2 === null || isNum(v.area_m2)) &&
    typeof v.outdoor === 'boolean' &&
    Array.isArray(v.points) &&
    v.points.every(isPair) &&
    isPair(v.label_at) &&
    (v.area_source === undefined || isOneOf(v.area_source, ['printed', 'outline', 'estimate'] as const)) &&
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
    (v.facts === undefined || isFacts(v.facts)) &&
    optStr(v.source_ext) &&
    optStr(v.project_id) &&
    optStr(v.floor_label) &&
    optNum(v.floor_order)
  )
}

function isCredit(v: unknown): v is Credit {
  return isObj(v) && isStr(v.text) && isStr(v.license) && optStr(v.url) && optStr(v.license_name)
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
    optStr(v.error) &&
    optStr(v.source_ext) &&
    optStr(v.project_id) &&
    optStr(v.floor_label) &&
    optNum(v.floor_order)
  )
}

export function isProjectFloor(v: unknown): v is ProjectFloor {
  return isObj(v) && isStr(v.drawing_id) && v.drawing_id.length > 0 && isStr(v.label) && v.label.length > 0 && isNum(v.order)
}

export function isProjectBundle(v: unknown): v is ProjectBundle {
  return (
    isObj(v) &&
    isStr(v.id) &&
    v.id.length > 0 &&
    isStr(v.title) &&
    v.title.length > 0 &&
    Array.isArray(v.floors) &&
    v.floors.every(isProjectFloor) &&
    (v.auto_matched === undefined || typeof v.auto_matched === 'boolean') &&
    optStr(v.created_at)
  )
}

export function isProjectList(v: unknown): v is ProjectList {
  return isObj(v) && isStr(v.contract_version) && Array.isArray(v.projects) && v.projects.every(isProjectBundle)
}

export function salvageProjectList(v: unknown): { list: unknown; dropped: number } {
  if (!isObj(v) || !Array.isArray(v.projects)) return { list: v, dropped: 0 }
  const projects = v.projects.filter(isProjectBundle)
  return { list: { ...v, projects }, dropped: v.projects.length - projects.length }
}

// ---- one bad item can't take down a list ----------------------------------------------------------

const evRoomOk = (r: unknown) =>
  isObj(r) &&
  isStr(r.room_id) &&
  typeof r.included === 'boolean' &&
  (r.area_m2 === null || isNum(r.area_m2)) &&
  (r.edited === undefined || typeof r.edited === 'boolean') &&
  optStr(r.group_id) &&
  optStr(r.drawing_id)
const evRowOk = (r: unknown) => isObj(r) && isStr(r.id) && optStr(r.name) && optNum(r.value)

/**
 * Drops the evidence boxes, rooms, rows, groups, links and trace nodes that fail validation instead of rejecting the
 * whole answer, and counts them in `dropped_items` so the card can say so. The answer itself (value, scope, …) is still
 * validated strictly by isAskResponse afterwards. Anything that is not a list is left as is for the guard to reject.
 */
export function salvageAskResponse(v: unknown): unknown {
  if (!isObj(v) || !isObj(v.evidence)) return v
  const ev = v.evidence
  let dropped = 0
  const keep = <T,>(list: unknown, ok: (x: unknown) => boolean): T[] | unknown => {
    if (!Array.isArray(list)) return list
    const out = list.filter(ok)
    dropped += list.length - out.length
    return out
  }
  const evidence = {
    ...ev,
    boxes: keep(ev.boxes, isBox),
    rooms: keep(ev.rooms, evRoomOk),
    ...(ev.rows !== undefined ? { rows: keep(ev.rows, evRowOk) } : {}),
    ...(ev.groups !== undefined ? { groups: keep(ev.groups, isEvidenceGroup) } : {}),
    ...(ev.links !== undefined ? { links: keep(ev.links, isEvidenceLink) } : {}),
  }
  const trace = keep(v.trace, isTraceNode)
  return dropped ? { ...v, evidence, trace, dropped_items: dropped } : v
}

/** Drawing summaries that fail validation are left out; the list stays. `dropped` says how many. */
export function salvageDrawingList(v: unknown): { list: unknown; dropped: number } {
  if (!isObj(v) || !Array.isArray(v.drawings)) return { list: v, dropped: 0 }
  const drawings = v.drawings.filter(isDrawingSummary)
  return { list: { ...v, drawings }, dropped: v.drawings.length - drawings.length }
}

/** Rooms that fail validation are left out of a drawing (their evidence then has no outline); the drawing stays. */
export function salvageDrawing(v: unknown): unknown {
  if (!isObj(v) || !Array.isArray(v.rooms)) return v
  const rooms = v.rooms.filter(isRoomPoly)
  return rooms.length === v.rooms.length ? v : { ...v, rooms }
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
    (['wc', 'bathtub', 'shower'] as const).every((k) => ev[k] === undefined || (Array.isArray(ev[k]) && (ev[k] as unknown[]).every(isBox))) &&
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

/** A readable line from FastAPI's 422 body ("String should have at most 2000 characters"), or null. Plain text only. */
export function fastApiMessage(v: unknown): string | null {
  if (!isFastApiDetail(v)) return null
  const d = (v as { detail: unknown }).detail
  if (typeof d === 'string') return d.slice(0, 300)
  if (Array.isArray(d)) {
    const first = d.find((x) => isObj(x) && isStr(x.msg)) as { msg: string } | undefined
    if (first) return first.msg.slice(0, 300)
  }
  return null
}

export function isHealth(v: unknown): v is Health {
  return (
    isObj(v) &&
    v.ok === true &&
    isStr(v.contract_version) &&
    (v.answerer === undefined || isOneOf(v.answerer, ANSWERER_MODES)) &&
    (v.agent_ok === undefined || typeof v.agent_ok === 'boolean')
  )
}

/** Major part of a "major.minor" version string. */
export function majorOf(version: string): string {
  return version.split('.')[0] ?? ''
}

export function isRewriteResponse(v: unknown): v is RewriteResponse {
  return (
    isObj(v) &&
    isStr(v.contract_version) &&
    isStr(v.drawing_id) &&
    isOneOf(v.detected_lang, LANGS) &&
    Array.isArray(v.blocks) &&
    v.blocks.every(
      (b) =>
        isObj(b) &&
        isStr(b.question) &&
        isOneOf(b.intent, ['count', 'area', 'other'] as const) &&
        isScope(b.scope) &&
        typeof b.scope_exists === 'boolean' &&
        typeof b.routable === 'boolean',
    ) &&
    Array.isArray(v.not_answerable) &&
    v.not_answerable.every(isStr) &&
    isNum(v.calls) &&
    isNum(v.cost_usd) &&
    isNum(v.tokens) &&
    isStr(v.model)
  )
}

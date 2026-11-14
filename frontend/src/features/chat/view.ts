import { usableDisplay } from '@/api/contract'
import type { AgentFailure, AskResponse, Box, Drawing, Lang, Method, RoomPoly, Scope } from '@/api/contract'
import { isBoxRejected, isRoomIncluded, roomEvidenceId, type Derived, type Toggles } from '@/lib/answer'
import { LOCALE, displayParts, fmtArea, fmtConverted, fmtCount, fmtRoomArea, noun } from '@/lib/format'
import { t, type StringKey, type UiKey } from '@/i18n/strings'

/** Pure view-model helpers for the answer card. Every number comes from the response or Derived. */

export const LANG_NAME_EN: Record<Lang, string> = { en: 'English', fr: 'French', fi: 'Finnish', de: 'German' }

export type GroupColorKey = 'c1' | 'c2' | 'c3' | 'c4' | 'c5' | 'c6'

export interface EvidenceGroupItem {
  id: string
  label: string
  color: GroupColorKey
  target_drawing_id?: string
}

export interface ProjectScopeLike {
  kind: 'project'
  project_id: string
  drawing_ids: string[]
}

const GROUP_CSS: Record<GroupColorKey, { solid: string; surface: string }> = {
  c1: { solid: 'var(--group-1, #1260b8)', surface: 'var(--group-1-surface, #ddeaf7)' },
  c2: { solid: 'var(--group-2, #6d3ab7)', surface: 'var(--group-2-surface, #efe7fb)' },
  c3: { solid: 'var(--group-3, #a82465)', surface: 'var(--group-3-surface, #fbe5f0)' },
  c4: { solid: 'var(--group-4, #0b6b8a)', surface: 'var(--group-4-surface, #dff3f9)' },
  c5: { solid: 'var(--group-5, #475569)', surface: 'var(--group-5-surface, #e8edf3)' },
  c6: { solid: 'var(--group-6, #7c4a15)', surface: 'var(--group-6-surface, #f6eadc)' },
}

export function groupCssVars(color: GroupColorKey | undefined): { solid: string; surface: string } | null {
  if (!color || !(color in GROUP_CSS)) return null
  return GROUP_CSS[color]
}

export function evidenceGroupsOf(resp: AskResponse): EvidenceGroupItem[] {
  const raw = (resp.evidence as { groups?: unknown }).groups
  if (!Array.isArray(raw)) return []
  return raw.filter(
    (g): g is EvidenceGroupItem =>
      typeof g === 'object' &&
      g !== null &&
      typeof (g as EvidenceGroupItem).id === 'string' &&
      typeof (g as EvidenceGroupItem).label === 'string' &&
      typeof (g as EvidenceGroupItem).color === 'string' &&
      (g as EvidenceGroupItem).color in GROUP_CSS,
  )
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

export function whereText(scope: Scope | ProjectScopeLike, lang: Lang): string {
  switch (scope.kind) {
    case 'floor':
      return t(lang, 'where_floor')
    case 'model':
      return t(lang, 'where_model')
    case 'unit':
      return t(lang, 'where_unit', { unit: scope.unit })
    case 'room':
      return t(lang, 'where_room', { room: scope.room_id })
    case 'label':
      return t(lang, 'where_label', { label: scope.label })
    case 'region':
      return t(lang, 'where_region', { label: scope.label })
    case 'project':
      return scope.drawing_ids.length > 0
        ? t(lang, 'where_project', { n: fmtCount(scope.drawing_ids.length, lang) })
        : t(lang, 'where_project_any')
  }
}

/** Scope chip under the user's question, in the answer's language ("Scoped to Area 1", "Limité à Area 1"). */
export function scopeChipText(scope: Scope | ProjectScopeLike, lang: Lang): string {
  if (scope.kind === 'region') return t(lang, 'scoped_to', { label: scope.label })
  return cap(whereText(scope, lang))
}

/**
 * The metric value behind an answer shown in another unit ("341.42 m²"), or null when the answer is
 * shown in metric anyway. Follows the user's toggles like the headline value.
 */
export function metricText(resp: AskResponse, d: Derived, lang: Lang): string | null {
  const display = usableDisplay(resp.display)
  if (!display) return null
  if (resp.answer.kind === 'area' && d.value !== null && display.factors.m2) return `${fmtArea(d.value, lang)} m²`
  if (resp.answer.kind === 'quantity' && d.value !== null && display.factors[resp.answer.unit])
    return `${fmtArea(d.value, lang)} ${{ m: 'm', m2: 'm²', m3: 'm³' }[resp.answer.unit]}`
  if (resp.answer.kind === 'note' && display.value !== undefined && display.metric_text) return display.metric_text
  return null
}

/** Formatted value with the unit/noun, e.g. ["12", "windows"] or ["139,34", "m²"]. */
export function valueParts(resp: AskResponse, d: Derived, lang: Lang): [string, string] | null {
  if (resp.answer.kind === 'text') return [resp.answer.value, t(lang, resp.answer.subject === 'fire_rating' ? 'text_fire_rating' : 'text_label')]
  const display = usableDisplay(resp.display)
  if (resp.answer.kind === 'note') {
    if (resp.answer.number !== undefined) {
      // A note's own unit may be anything ("mm"): the server's converted text is the value to show.
      if (display?.text && display.value !== undefined) {
        if (display.style === 'ft_in') return [display.text, '']
        return [fmtConverted(display.value, lang), display.label]
      }
      const unit = resp.answer.unit ?? ''
      const isAreaUnit = unit === 'm²' || unit === 'm2'
      const numStr = !isAreaUnit && Number.isInteger(resp.answer.number)
        ? fmtCount(resp.answer.number, lang)
        : fmtArea(resp.answer.number, lang)
      return [numStr, unit]
    }
    return [resp.answer.value, '']
  }
  if (d.value === null) return null
  if (resp.answer.kind === 'count') return [fmtCount(d.value, lang), noun(resp.answer.subject, d.value, lang)]
  if (resp.answer.kind === 'area') {
    const shown = displayParts(d.value, 'm2', display, lang)
    if (shown) return shown
    return resp.answer.approximate
      ? [`≈ ${fmtCount(Math.round(d.value), lang)}`, 'm² (estimate)']
      : [fmtArea(d.value, lang), 'm²']
  }
  if (resp.answer.kind === 'quantity')
    return displayParts(d.value, resp.answer.unit, display, lang) ?? [fmtArea(d.value, lang), { m: 'm', m2: 'm²', m3: 'm³' }[resp.answer.unit]]
  return null
}

const fmtFor = (resp: AskResponse, n: number, lang: Lang) =>
  resp.answer.kind === 'area' || resp.answer.kind === 'quantity' || (resp.answer.kind === 'note' && !Number.isInteger(n))
    ? fmtArea(n, lang)
    : fmtCount(n, lang)

export function sentence(resp: AskResponse, d: Derived, drawing: Drawing, lang: Lang): string {
  const where = whereText(resp.scope as Scope | ProjectScopeLike, lang)
  if (resp.answer.kind === 'count') return t(lang, 'counted_where', { where })
  // A length read from a printed dimension or measured on the outline says so itself (its explanation); only an
  // IFC quantity is "as given by the model file".
  if (resp.answer.kind === 'quantity' && resp.explanation && drawing.kind !== 'ifc') return ''
  if (resp.answer.kind === 'quantity') return t(lang, 'measured_where', { where })
  if (resp.answer.kind === 'text') return t(lang, 'text_found')
  // A note is only an agent estimate when its method says so; exact notes (e.g. "largest room") come from CAD.
  if (resp.answer.kind === 'note')
    return t(lang, resp.method === 'agent_estimate' ? 'agent_note_found' : resp.method === 'cad_geometry' ? 'note_exact' : 'note_read')
  if (resp.answer.kind !== 'area') return ''
  const byId = new Map(drawing.rooms.map((r) => [r.room_id, r]))
  const parts = [t(lang, 'area_where', { where })]
  if (resp.answer.approximate) parts.push(t(lang, 'area_estimate_note'))
  if (resp.evidence.rooms.length > 0) {
    parts.push(t(lang, 'rooms_included', { n: fmtCount(d.includedRooms.length, lang) }))
  } else if (resp.method === 'agent_estimate') {
    parts.push(t(lang, 'agent_note_found'))
  }
  const outdoorLeftOut = resp.evidence.rooms.some(
    (r) => byId.get(r.room_id)?.outdoor && !d.includedRooms.some((x) => x.room_id === r.room_id),
  )
  if (outdoorLeftOut) parts.push(t(lang, 'outdoor_left_out'))
  for (const r of resp.evidence.rooms) if (r.edited) parts.push(t(lang, 'whatif_used', { room: r.room_id }))
  return parts.join(' ')
}

export type ChipTone = 'success' | 'info' | 'warning' | 'destructive' | 'neutral'
export interface Chip {
  key: string
  text: string
  tone: ChipTone
  outline?: boolean
  title?: string
  /** Tapping the chip locates this evidence on the plan. */
  evidenceId?: string
}

const METHOD_KEY: Record<Exclude<Method, 'cad_geometry'>, StringKey> = {
  ocr: 'method_ocr',
  detector: 'method_detector',
  vision: 'method_vision',
  agent_estimate: 'method_agent',
  pdf_vector: 'method_pdf_vector',
  store_sql: 'method_store_sql',
  none: 'method_none',
}

export function chips(resp: AskResponse, d: Derived, lang: Lang): Chip[] {
  const out: Chip[] = []
  if (resp.method === 'cad_geometry') out.push({ key: 'method', text: t(lang, 'chip_exact'), tone: 'success' })
  // No model involved: exact-style chip, unless the answer carries an estimate (then it keeps the estimate styling).
  else if ((resp.method === 'pdf_vector' || resp.method === 'store_sql') && !resp.estimate)
    out.push({ key: 'method', text: t(lang, METHOD_KEY[resp.method]), tone: 'success' })
  else if (resp.method === 'agent_estimate') {
    out.push({ key: 'method', text: t(lang, 'chip_agent_estimate'), tone: 'info' })
    if (resp.cost_usd > 0 || resp.calls > 0) {
      out.push({
        key: 'cost',
        text: t(lang, 'cost_upper_bound', { cost: fmtUsd(resp.cost_usd) }),
        tone: 'neutral',
        outline: true,
      })
    }
  } else
    // A vision count is a model estimate (plan principle: model-detected items are always labelled as estimates).
    out.push({ key: 'method', text: t(lang, METHOD_KEY[resp.method]), tone: resp.method === 'vision' ? 'info' : 'neutral' })

  const c = resp.check
  if (c?.image === 'none')
    // DXF uploads: checking our own render would only check the renderer, so there is no check.
    out.push({ key: 'check', text: t(lang, 'chip_no_image'), tone: 'neutral', outline: true })
  else if (c?.status === 'sample')
    out.push({
      key: 'check',
      text: t(lang, 'chip_vision_sample'),
      tone: 'neutral',
      outline: true,
      title:
        'No check result. Answers computed in your browser are not checked; on the server, a check that failed or timed out gives no numbers (its cost is still counted).',
    })
  else if (c?.status === 'live' && c.ranking?.length) {
    // Ranked check (1.1): no fixed-threshold "agree" count; say what was checked and point at the weakest box.
    const i = c.least_certain_id ? resp.evidence.boxes.findIndex((b) => b.id === c.least_certain_id) : -1
    out.push({
      key: 'check',
      text:
        i >= 0
          ? t(lang, 'chip_checked_least', { n: fmtCount(c.ranking.length, lang), k: String(i + 1) })
          : t(lang, 'chip_checked', { n: fmtCount(c.ranking.length, lang) }),
      tone: 'info',
      outline: true,
      title: 'Gemini Decisions checked the drawn boxes against the dataset image. It cannot find objects the geometry missed.',
      ...(i >= 0 ? { evidenceId: c.least_certain_id } : {}),
    })
  } else if (c?.status === 'live' && c.agreed !== undefined && c.total !== undefined)
    out.push({
      key: 'check',
      text: t(lang, 'chip_vision_live', { agreed: fmtCount(c.agreed, lang), total: fmtCount(c.total, lang) }),
      tone: 'info',
      outline: true,
    })

  const g = d.gold
  if (g.state !== 'none' && g.dataset !== undefined) {
    const ds = fmtFor(resp, g.dataset, lang)
    const ours = d.value === null ? '' : fmtFor(resp, d.value, lang)
    const label = t(lang, 'chip_dataset')
    const title = g.dataset_qid ? `Dataset question ${g.dataset_qid}` : undefined
    if (g.state === 'match') out.push({ key: 'gold', text: `${label}: ${ds} ✓`, tone: 'success', title })
    else if (g.state === 'within_tolerance')
      out.push({
        key: 'gold',
        text: `${label} ${ds} · ${t(lang, 'chip_ours')} ${ours} ✓ ${t(lang, 'chip_within')}`,
        tone: 'success',
        title,
      })
    else if (g.state === 'mismatch')
      out.push({ key: 'gold', text: `${label} ${ds} · ${t(lang, 'chip_ours')} ${ours}`, tone: 'destructive', title })
    else if (g.state === 'user_changed')
      out.push({ key: 'gold', text: `${label}: ${ds} · ${t(lang, 'chip_you', { n: ours })}`, tone: 'warning', title })
  }
  return out
}

export interface Row {
  id: string
  badge: string
  title: string
  sub: string
  /** Counted / added (true) vs rejected / left out (false). */
  on: boolean
  state: string
  /** element: an IFC row counted without a 2D box (nothing to show or toggle on the plan). */
  kind: 'box' | 'room' | 'element'
  edited?: boolean
  /** Hover text for elements: the IFC GlobalId. */
  hint?: string
  /** The solver's note, verbatim (neutral secondary line). */
  note?: string
  /** Underlying box geometry for inline 56x56 blueprint crop previews. */
  box?: Box
  /** True when Gemini ranked check flagged this box as least_certain_id. */
  leastCertain?: boolean
  /** Optional categorical group metadata (`c1..c6`). */
  groupId?: string
  groupColor?: GroupColorKey
  groupLabel?: string
  /** Optional cross-floor target drawing id & short floor label (`EG`, `1. OG`, …). */
  drawingId?: string
  floorLabel?: string
}

/** "Drawn twice" only from the explicit `duplicate_of` signal (contract 1.1), never from the presence of a note. */
export function duplicateNote(box: Box, boxes: Box[]): 'leaf_1' | 'leaf_2' | null {
  if (box.duplicate_of) return 'leaf_2'
  return boxes.some((b) => b.duplicate_of === box.id) ? 'leaf_1' : null
}

function roomLabel(room: RoomPoly | undefined, id: string | undefined): string {
  if (!room) return id ?? ''
  return `${room.name} ${room.num}`
}

export function rows(
  resp: AskResponse,
  toggles: Toggles,
  drawing: Drawing,
  lang: Lang,
  floorLabels?: ReadonlyMap<string, string>,
): Row[] {
  const byId = new Map(drawing.rooms.map((r) => [r.room_id, r]))
  const groupById = new Map(evidenceGroupsOf(resp).map((g) => [g.id, g]))
  const leastId = resp.check?.least_certain_id
  const boxes = resp.evidence.boxes.map((b, i): Row => {
    const on = !isBoxRejected(b.id, toggles)
    const leaf = duplicateNote(b, resp.evidence.boxes)
    const room = b.room_id ? byId.get(b.room_id) : undefined
    const bExtra = b as Box & { group_id?: string; drawing_id?: string }
    const grp = bExtra.group_id ? groupById.get(bExtra.group_id) : undefined
    const drawingId = bExtra.drawing_id
    const floorLabel = drawingId && drawingId !== drawing.id ? (floorLabels?.get(drawingId) ?? drawingId) : undefined
    return {
      id: b.id,
      kind: 'box',
      badge: String(i + 1),
      title: `${cap(noun(b.subject, 1, lang))} ${i + 1}${leaf ? ` · ${t(lang, leaf as 'leaf_1' | 'leaf_2')}` : ''}`,
      sub: [roomLabel(room, b.room_id), b.unit ? `${b.unit}` : '', grp && !b.unit ? grp.label : ''].filter(Boolean).join(' · '),
      // A duplicate's note only repeats its label; any other note is shown as is.
      ...(b.note && !leaf ? { note: b.note } : {}),
      box: b,
      ...(leastId === b.id ? { leastCertain: true } : {}),
      ...(grp ? { groupId: grp.id, groupColor: grp.color, groupLabel: grp.label } : {}),
      ...(drawingId ? { drawingId } : {}),
      ...(floorLabel ? { floorLabel } : {}),
      on,
      state: on ? t(lang, 'counted') : t(lang, 'rejected'),
    }
  })
  const rooms = resp.evidence.rooms.map((r): Row => {
    const on = isRoomIncluded(r, toggles)
    const poly = byId.get(r.room_id)
    const conv = r.area_m2 === null ? null : displayParts(r.area_m2, 'm2', usableDisplay(resp.display), lang)
    // an area measured under an estimated scale reads "≈ 12 m² (estimate)", as the headline does
    const estimated = !r.edited && poly?.area_source === 'estimate'
    const metric = r.area_m2 === null ? '' : fmtRoomArea(r.area_m2, estimated ? 'estimate' : undefined, lang)
    const area =
      r.area_m2 === null
        ? t(lang, 'area_not_printed')
        : conv
          ? `${estimated ? '≈ ' : ''}${conv.join(' ')} (${metric})`
          : metric
    const rExtra = r as typeof r & { group_id?: string; drawing_id?: string }
    const grp = rExtra.group_id ? groupById.get(rExtra.group_id) : undefined
    const drawingId = rExtra.drawing_id
    const floorLabel = drawingId && drawingId !== drawing.id ? (floorLabels?.get(drawingId) ?? drawingId) : undefined
    return {
      id: roomEvidenceId(r.room_id),
      kind: 'room',
      badge: poly?.num ?? r.room_id,
      title: `${poly?.name ?? r.room_id}${poly?.outdoor ? ` (${t(lang, 'outdoor')})` : ''}`,
      sub: `${on && r.area_m2 !== null ? '+ ' : ''}${area}${r.edited ? ` · ${t(lang, 'your_whatif_size')}` : ''}${grp ? ` · ${grp.label}` : ''}`,
      ...(grp ? { groupId: grp.id, groupColor: grp.color, groupLabel: grp.label } : {}),
      ...(drawingId ? { drawingId } : {}),
      ...(floorLabel ? { floorLabel } : {}),
      on,
      state: on ? t(lang, 'added') : t(lang, 'left_out'),
      edited: r.edited,
    }
  })
  const before = boxes.length + rooms.length
  const elements = (resp.evidence.rows ?? []).map((e, i): Row => ({
    id: `row-${e.id}`,
    kind: 'element',
    badge: String(before + i + 1),
    title: e.name || e.id,
    sub: [e.value !== undefined ? fmtArea(e.value, lang) : '', t(lang, 'no_box')].filter(Boolean).join(' · '),
    hint: `IFC GlobalId ${e.id}`,
    on: true,
    state: t(lang, 'counted'),
  }))
  return [...boxes, ...rooms, ...elements]
}

/** Cost rounded to the precision we display (4 dp), so the header sum equals the sum of the footers. */
export const roundCost = (usd: number) => Math.round(usd * 10_000) / 10_000

/** "$0.00" for nothing spent; cents when exact, otherwise 4 decimals. */
export function fmtUsd(usd: number): string {
  const v = roundCost(usd)
  if (v === 0) return '$0.00'
  // From a dollar up, cents are enough.
  if (v >= 1) return `$${v.toFixed(2)}`
  return Math.round(v * 100) / 100 === v ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`
}

const plural = new Map<Lang, Intl.PluralRules>()

/** "1 model call", "10,000 model calls", "22 mallikutsua". */
export function callsText(n: number, lang: Lang): string {
  let pr = plural.get(lang)
  if (!pr) {
    pr = new Intl.PluralRules(LOCALE[lang])
    plural.set(lang, pr)
  }
  return t(lang, pr.select(n) === 'one' ? 'call_one' : 'call_other', { n: fmtCount(n, lang) })
}

const METHOD_FOOTER: Record<Method, StringKey> = {
  cad_geometry: 'method_cad',
  ocr: 'method_ocr',
  detector: 'method_detector',
  vision: 'method_vision',
  agent_estimate: 'method_agent',
  pdf_vector: 'method_pdf_vector',
  store_sql: 'method_store_sql',
  none: 'method_none',
}

/** Footer parts, built only from response fields, in the answer's language. */
/** Requests and cost of the answer's model check (contract 1.1); empty when the response does not report them. */
export function checkParts(resp: AskResponse, lang: Lang): string[] {
  const c = resp.check
  if (!c || (c.calls === undefined && c.cached === undefined)) return []
  const label = `${t(lang, 'footer_check')}:`
  if (c.cached || c.calls === 0) return [`${label} ${t(lang, 'check_cached')}`, fmtUsd(c.cost_usd ?? 0)]
  const n = c.calls ?? 0
  const pr = plural.get(lang) ?? new Intl.PluralRules(LOCALE[lang])
  plural.set(lang, pr)
  const parts = [`${label} ${t(lang, pr.select(n) === 'one' ? 'req_one' : 'req_other', { n: fmtCount(n, lang) })}`]
  if (c.tokens) parts.push(t(lang, 'tokens', { n: fmtTokens(c.tokens, lang) }))
  parts.push(fmtUsd(c.cost_usd ?? 0))
  return parts
}

/** 13 612 → "13.6k" (en) / "13,6 k" style per locale. */
function fmtTokens(n: number, lang: Lang): string {
  if (n < 1000) return fmtCount(n, lang)
  return `${new Intl.NumberFormat(LOCALE[lang], { maximumFractionDigits: 1 }).format(n / 1000)}k`
}

export function footerParts(resp: AskResponse, lang: Lang): string[] {
  const costText =
    resp.method === 'agent_estimate' && (resp.cost_usd > 0 || resp.calls > 0)
      ? t(lang, 'cost_upper_bound', { cost: fmtUsd(resp.cost_usd) })
      : fmtUsd(resp.cost_usd)
  return [
    `${t(lang, 'footer_answer')}: ${t(lang, METHOD_FOOTER[resp.method])}`,
    callsText(resp.calls, lang),
    costText,
    ...checkParts(resp, lang),
    `${fmtCount(resp.latency_ms, lang)} ms`,
    t(lang, resp.source === 'local' ? 'source_local' : 'source_api'),
  ]
}

/** One-line English announcement for the live region. */
export function announce(resp: AskResponse, d: Derived): string {
  if (resp.answer.kind === 'abstain') {
    const r = resp.answer.reason
    if (r === 'not_on_drawing') return 'Not on this drawing. Nothing guessed.'
    if (r === 'not_supported_yet') return 'Fixtures of this kind are not extracted yet. Nothing guessed.'
    if (r === 'low_confidence') return 'Not sure enough to say. Nothing stated as a count.'
    if (r === 'not_answerable') return 'Not in this drawing’s data. Nothing guessed.'
    return 'The question could not be matched to this drawing.'
  }
  const v = valueParts(resp, d, 'en')
  const gold: Record<string, string> = {
    match: ' Dataset match.',
    within_tolerance: ' Within 2 % of the dataset.',
    mismatch: ' Differs from the dataset.',
    user_changed: ' Changed by you.',
    none: '',
  }
  return v ? `${v[0]} ${v[1]}.${gold[d.gold.state]}` : ''
}

export type SlashCommandId =
  | 'clear'
  | 'export'
  | 'trace'
  | 'compare'
  | 'all-floors'
  | 'fit'
  | 'zoom'
  | 'select'
  | 'mark'
  | 'whatif'
  | 'undo'
  | 'help'
  | 'color'
  | 'stairs'
  | 'summary'
  | 'new'
  | 'history'

export interface SlashCommandSpec {
  id: SlashCommandId
  /** Canonical slash command, e.g. "/clear" or "/zoom". */
  cmd: string
  /** Optional argument hint displayed beside the command in the palette. */
  argHint?: string
  /** Accepted aliases without the leading "/". */
  aliases: readonly string[]
  /** Concise action summary shown in the slash listbox. */
  description: string
  /** True when Tab-completion should append a trailing space for arguments. */
  acceptsArgs?: boolean
  /** Interface-language key for the description (shown instead of `description` when set). */
  descriptionKey?: UiKey
}

export const SLASH_COMMANDS: readonly SlashCommandSpec[] = [
  { id: 'clear', cmd: '/clear', aliases: ['reset'], description: 'Clear conversation (with Undo)', descriptionKey: 'slash_clear_desc' },
  {
    id: 'new',
    cmd: '/new',
    aliases: [],
    description: 'New conversation (the current one is saved to History)',
    descriptionKey: 'slash_new_desc',
  },
  {
    id: 'history',
    cmd: '/history',
    aliases: ['sessions'],
    description: 'Open session history',
    descriptionKey: 'slash_history_desc',
  },
  {
    id: 'color',
    cmd: '/color',
    argHint: '[by]',
    aliases: ['colour', 'colors'],
    description: 'Color-code apartments or prior chat items',
    descriptionKey: 'slash_color_desc',
    acceptsArgs: true,
  },
  { id: 'stairs', cmd: '/stairs', aliases: ['stair'], description: 'Show all stairs and where they lead in colors', descriptionKey: 'slash_stairs_desc' },
  { id: 'summary', cmd: '/summary', aliases: ['breakdown'], description: 'Complete breakdown of rooms, area, doors & windows', descriptionKey: 'slash_summary_desc' },
  { id: 'all-floors', cmd: '/all-floors', aliases: ['floors'], description: 'Toggle cross-plan matching across all floors', descriptionKey: 'slash_all_floors_desc' },
  { id: 'export', cmd: '/export', aliases: ['csv'], description: 'Download active answer evidence as CSV', descriptionKey: 'slash_export_desc' },
  { id: 'trace', cmd: '/trace', aliases: ['tree'], description: 'Toggle reasoning tree panel', descriptionKey: 'slash_trace_desc' },
  { id: 'compare', cmd: '/compare', aliases: ['diff'], description: 'Toggle original vs rebuilt comparison', descriptionKey: 'slash_compare_desc' },
  { id: 'fit', cmd: '/fit', aliases: [], description: 'Reset drawing zoom to 100 %', descriptionKey: 'slash_fit_desc' },
  {
    id: 'zoom',
    cmd: '/zoom',
    argHint: '<100..450>',
    aliases: [],
    description: 'Set drawing zoom percentage (100–450 %)',
    descriptionKey: 'slash_zoom_desc',
    acceptsArgs: true,
  },
  { id: 'select', cmd: '/select', aliases: [], description: 'Switch to Select tool', descriptionKey: 'slash_select_desc' },
  { id: 'mark', cmd: '/mark', aliases: [], description: 'Switch to Mark area tool', descriptionKey: 'slash_mark_desc' },
  { id: 'whatif', cmd: '/whatif', aliases: ['what-if'], description: 'Switch to What-if room resize tool', descriptionKey: 'slash_whatif_desc' },
  { id: 'undo', cmd: '/undo', aliases: [], description: 'Undo last evidence toggle', descriptionKey: 'slash_undo_desc' },
  { id: 'help', cmd: '/help', aliases: ['shortcuts', '?'], description: 'Open keyboard shortcuts cheatsheet', descriptionKey: 'slash_help_desc' },
]

export type ParsedSlashAction =
  | {
      kind: 'client'
      id: Exclude<SlashCommandId, 'zoom' | 'color' | 'stairs' | 'summary'>
    }
  | { kind: 'client'; id: 'zoom'; zoom: number }
  | { kind: 'prompt'; id: 'color' | 'stairs' | 'summary'; prompt: string }
  | { kind: 'invalid'; id: SlashCommandId; message: string; messageKey?: UiKey }

/** Filter slash palette candidates while the composer starts with `/` and has no newline. */
export function filterSlashCommands(input: string): SlashCommandSpec[] {
  if (!input.startsWith('/') || input.includes('\n')) return []
  const body = input.slice(1)
  const spaceIdx = body.search(/\s/)
  const token = (spaceIdx < 0 ? body : body.slice(0, spaceIdx)).toLowerCase()
  if (!token) return [...SLASH_COMMANDS]
  if (spaceIdx >= 0) {
    return SLASH_COMMANDS.filter((c) => c.cmd.slice(1) === token || c.aliases.includes(token))
  }
  return SLASH_COMMANDS.filter(
    (c) => c.cmd.slice(1).startsWith(token) || c.aliases.some((a) => a.startsWith(token)),
  )
}

/** Resolve an exact slash command line into either an instant client action or a smart contextual prompt. */
export function resolveSlashCommand(input: string, ctx: { hasTurns: boolean }): ParsedSlashAction | null {
  const trimmed = input.trim()
  if (!trimmed.startsWith('/') || trimmed.includes('\n')) return null
  const body = trimmed.slice(1)
  if (!body) return null
  const spaceIdx = body.search(/\s/)
  const token = (spaceIdx < 0 ? body : body.slice(0, spaceIdx)).toLowerCase()
  const arg = spaceIdx < 0 ? '' : body.slice(spaceIdx + 1).trim()
  const spec = SLASH_COMMANDS.find((c) => c.cmd.slice(1) === token || c.aliases.includes(token))
  if (!spec) return null

  switch (spec.id) {
    case 'clear':
    case 'export':
    case 'trace':
    case 'compare':
    case 'all-floors':
    case 'fit':
    case 'select':
    case 'mark':
    case 'whatif':
    case 'undo':
    case 'help':
    case 'new':
    case 'history':
      return { kind: 'client', id: spec.id }
    case 'zoom': {
      if (!arg) return { kind: 'client', id: 'fit' }
      const rawNum = Number(arg.replace(/%$/, '').trim())
      if (!Number.isFinite(rawNum)) {
        return { kind: 'invalid', id: 'zoom', message: 'Usage: /zoom <100..450>', messageKey: 'slash_zoom_usage' }
      }
      const clamped = Math.min(450, Math.max(100, Math.round(rawNum)))
      return { kind: 'client', id: 'zoom', zoom: clamped }
    }
    case 'color': {
      if (arg) {
        const criteria = arg.replace(/^by\s+/i, '').trim() || arg
        const prompt = ctx.hasTurns
          ? `Color-code these into different colors by ${criteria}`
          : `Show and color-code items on this floor plan in different colors by ${criteria}`
        return { kind: 'prompt', id: 'color', prompt }
      }
      const prompt = ctx.hasTurns
        ? 'Color-code these into different colors'
        : 'How many apartments are there? Color-code each in different colors'
      return { kind: 'prompt', id: 'color', prompt }
    }
    case 'stairs': {
      const prompt = arg
        ? `Show all stairs and where they lead to in different colors (${arg})`
        : 'Show all stairs and where they lead to in different colors'
      return { kind: 'prompt', id: 'stairs', prompt }
    }
    case 'summary':
      return {
        kind: 'prompt',
        id: 'summary',
        prompt: 'Give a complete breakdown of rooms, total area, doors, and windows on this floor plan',
      }
  }
}


export type { AgentFailure } from '@/api/contract'

/** A failed deep-agent run (service down, deadline, run error), as the backend labels it in `agent_failure`. */
export function agentFailure(resp: AskResponse): AgentFailure | null {
  return resp.answer.kind === 'abstain' ? (resp.agent_failure ?? null) : null
}

/** What the exact router, tools and SQL tried, from the trace's `look` node (English, from the server); null when absent. */
export function triedText(resp: AskResponse): string | null {
  const look = resp.trace.find((n) => n.id === 'look')
  const text = look?.body.replace(/^I cannot answer this from the drawing.s data:?\s*/i, '').trim()
  if (!text) return null
  return text.length > 240 ? `${text.slice(0, 237)}…` : text
}

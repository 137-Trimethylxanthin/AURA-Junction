import type { AskResponse, Box, Drawing, Lang, Method, RoomPoly, Scope } from '@/api/contract'
import { isBoxRejected, isRoomIncluded, roomEvidenceId, type Derived, type Toggles } from '@/lib/answer'
import { LOCALE, fmtArea, fmtCount, noun } from '@/lib/format'
import { t, type StringKey } from '@/i18n/strings'

/** Pure view-model helpers for the answer card. Every number comes from the response or Derived. */

export const LANG_NAME_EN: Record<Lang, string> = { en: 'English', fr: 'French', fi: 'Finnish', de: 'German' }

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

export function whereText(scope: Scope, lang: Lang): string {
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
  }
}

/** Scope chip under the user's question, in the answer's language ("Scoped to Area 1", "Limité à Area 1"). */
export function scopeChipText(scope: Scope, lang: Lang): string {
  if (scope.kind === 'region') return t(lang, 'scoped_to', { label: scope.label })
  return cap(whereText(scope, lang))
}

/** Formatted value with the unit/noun, e.g. ["12", "windows"] or ["139,34", "m²"]. */
export function valueParts(resp: AskResponse, d: Derived, lang: Lang): [string, string] | null {
  if (resp.answer.kind === 'text') return [resp.answer.value, t(lang, resp.answer.subject === 'fire_rating' ? 'text_fire_rating' : 'text_label')]
  if (d.value === null) return null
  if (resp.answer.kind === 'count') return [fmtCount(d.value, lang), noun(resp.answer.subject, d.value, lang)]
  if (resp.answer.kind === 'area') return [fmtArea(d.value, lang), 'm²']
  if (resp.answer.kind === 'quantity')
    return [fmtArea(d.value, lang), { m: 'm', m2: 'm²', m3: 'm³' }[resp.answer.unit]]
  return null
}

const fmtFor = (resp: AskResponse, n: number, lang: Lang) =>
  resp.answer.kind === 'area' || resp.answer.kind === 'quantity' ? fmtArea(n, lang) : fmtCount(n, lang)

export function sentence(resp: AskResponse, d: Derived, drawing: Drawing, lang: Lang): string {
  const where = whereText(resp.scope, lang)
  if (resp.answer.kind === 'count') return t(lang, 'counted_where', { where })
  if (resp.answer.kind === 'quantity') return t(lang, 'measured_where', { where })
  if (resp.answer.kind === 'text') return t(lang, 'text_found')
  if (resp.answer.kind !== 'area') return ''
  const byId = new Map(drawing.rooms.map((r) => [r.room_id, r]))
  const parts = [t(lang, 'area_where', { where }), t(lang, 'rooms_included', { n: fmtCount(d.includedRooms.length, lang) })]
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

const METHOD_KEY: Record<Exclude<Method, 'cad_geometry'>, 'method_ocr' | 'method_detector' | 'method_vision' | 'method_none'> = {
  ocr: 'method_ocr',
  detector: 'method_detector',
  vision: 'method_vision',
  none: 'method_none',
}

export function chips(resp: AskResponse, d: Derived, lang: Lang): Chip[] {
  const out: Chip[] = []
  if (resp.method === 'cad_geometry') out.push({ key: 'method', text: t(lang, 'chip_exact'), tone: 'success' })
  else out.push({ key: 'method', text: t(lang, METHOD_KEY[resp.method]), tone: 'neutral' })

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
      title: 'Sample value. The Gemini Decisions API check is not wired yet.',
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
  kind: 'box' | 'room'
  edited?: boolean
}

function leafNote(box: Box, i: number, boxes: Box[]): string | null {
  if (!box.note) return null
  const prev = boxes[i - 1]
  return prev && prev.note && prev.x === box.x && prev.y === box.y ? 'leaf_2' : 'leaf_1'
}

function roomLabel(room: RoomPoly | undefined, id: string | undefined): string {
  if (!room) return id ?? ''
  return `${room.name} ${room.num}`
}

export function rows(resp: AskResponse, toggles: Toggles, drawing: Drawing, lang: Lang): Row[] {
  const byId = new Map(drawing.rooms.map((r) => [r.room_id, r]))
  const boxes = resp.evidence.boxes.map((b, i): Row => {
    const on = !isBoxRejected(b.id, toggles)
    const leaf = leafNote(b, i, resp.evidence.boxes)
    const room = b.room_id ? byId.get(b.room_id) : undefined
    return {
      id: b.id,
      kind: 'box',
      badge: String(i + 1),
      title: `${cap(noun(b.subject, 1, lang))} ${i + 1}${leaf ? ` · ${t(lang, leaf as 'leaf_1' | 'leaf_2')}` : ''}`,
      sub: [roomLabel(room, b.room_id), b.unit ? `${b.unit}` : ''].filter(Boolean).join(' · '),
      on,
      state: on ? t(lang, 'counted') : t(lang, 'rejected'),
    }
  })
  const rooms = resp.evidence.rooms.map((r): Row => {
    const on = isRoomIncluded(r, toggles)
    const poly = byId.get(r.room_id)
    const area = `${fmtArea(r.area_m2, lang)} m²`
    return {
      id: roomEvidenceId(r.room_id),
      kind: 'room',
      badge: poly?.num ?? r.room_id,
      title: `${poly?.name ?? r.room_id}${poly?.outdoor ? ` (${t(lang, 'outdoor')})` : ''}`,
      sub: `${on ? '+ ' : ''}${area}${r.edited ? ` · ${t(lang, 'your_whatif_size')}` : ''}`,
      on,
      state: on ? t(lang, 'added') : t(lang, 'left_out'),
      edited: r.edited,
    }
  })
  return [...boxes, ...rooms]
}

/** Cost rounded to the precision we display (4 dp), so the header sum equals the sum of the footers. */
export const roundCost = (usd: number) => Math.round(usd * 10_000) / 10_000

/** "$0.00" for nothing spent; cents when exact, otherwise 4 decimals. */
export function fmtUsd(usd: number): string {
  const v = roundCost(usd)
  if (v === 0) return '$0.00'
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
  return [
    `${t(lang, 'footer_answer')}: ${t(lang, METHOD_FOOTER[resp.method])}`,
    callsText(resp.calls, lang),
    fmtUsd(resp.cost_usd),
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


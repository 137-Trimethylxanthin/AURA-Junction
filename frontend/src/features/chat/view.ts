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
  if (d.value === null) return null
  if (resp.answer.kind === 'count') return [fmtCount(d.value, lang), noun(resp.answer.subject, d.value, lang)]
  if (resp.answer.kind === 'area') return [fmtArea(d.value, lang), 'm²']
  return null
}

const fmtFor = (resp: AskResponse, n: number, lang: Lang) =>
  resp.answer.kind === 'area' ? fmtArea(n, lang) : fmtCount(n, lang)

export function sentence(resp: AskResponse, d: Derived, drawing: Drawing, lang: Lang): string {
  const where = whereText(resp.scope, lang)
  if (resp.answer.kind === 'count') return t(lang, 'counted_where', { where })
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
  if (c?.status === 'sample')
    out.push({
      key: 'check',
      text: t(lang, 'chip_vision_sample'),
      tone: 'neutral',
      outline: true,
      title: 'Sample value. The Gemini Decisions API check is not wired yet.',
    })
  else if (c?.status === 'live' && c.agreed !== undefined && c.total !== undefined)
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
export function footerParts(resp: AskResponse, lang: Lang): string[] {
  return [
    `${t(lang, 'footer_answer')}: ${t(lang, METHOD_FOOTER[resp.method])}`,
    callsText(resp.calls, lang),
    fmtUsd(resp.cost_usd),
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


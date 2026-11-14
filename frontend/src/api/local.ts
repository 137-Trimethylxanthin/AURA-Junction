/**
 * Offline snapshot of the backend's router + answer builder, for the bundled drawing only.
 * The backend (backend/aura/router.py, answer.py) is authoritative; this side follows it, checked by the shared
 * golden table (conformance.fixtures.ts / .json, also run by backend/tests/test_contract.py). See contract.md "Conformance".
 */
import {
  CONTRACT_VERSION,
  GROUP_COLORS,
  type AbstainReason,
  type AskRequest,
  type AskResponse,
  type Box,
  type BoxSubject,
  type DrawingFile,
  type EvidenceGroup,
  type EvidenceRoom,
  type Gold,
  type Lang,
  type RoomPoly,
  type Scope,
  type Subject,
  type TraceNode,
} from './contract'
import { bundledDrawing } from '@/data/drawing'
import { round2, roomEvidenceId } from '@/lib/answer'
import { detect, hasLangSignal } from '@/lib/detect'
import { noun } from '@/lib/format'
import { boxCentreInRect, pointInRect } from '@/lib/geometry'
import { route, type NotOnDrawingTopic } from '@/lib/route'
import { t, type StringKey } from '@/i18n/strings'

const BOX_SUBJECTS: readonly BoxSubject[] = ['window', 'door', 'washbasin', 'wc', 'bathtub', 'shower']
const isBoxSubject = (s: Subject): s is BoxSubject => (BOX_SUBJECTS as readonly string[]).includes(s)

const LANG_NAME: Record<Lang, string> = { en: 'English', fr: 'French', fi: 'Finnish', de: 'German' }

const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now())

function whereEn(scope: Scope): string {
  switch (scope.kind) {
    case 'floor':
      return 'on this floor plan'
    case 'model':
      return 'in the whole model'
    case 'unit':
      return `in apartment ${scope.unit}`
    case 'room':
      return `in room ${scope.room_id}`
    case 'label':
      return `in rooms labelled ${scope.label}`
    case 'region':
      return `inside ${scope.label}`
    case 'project':
      return `across ${scope.drawing_ids.length} floors`
  }
}

function scopeText(scope: Scope): string {
  switch (scope.kind) {
    case 'floor':
      return 'whole floor'
    case 'model':
      return 'whole model'
    case 'unit':
      return `apartment ${scope.unit}`
    case 'room':
      return `room ${scope.room_id}`
    case 'label':
      return `rooms labelled ${scope.label}`
    case 'region':
      return scope.label
    case 'project':
      return `all ${scope.drawing_ids.length} floors`
  }
}

function sameScope(a: Scope, b: Scope): boolean {
  if (a.kind !== b.kind) return false
  switch (a.kind) {
    case 'floor':
    case 'model':
      return true
    case 'unit':
      return a.unit === (b as typeof a).unit
    case 'room':
      return a.room_id === (b as typeof a).room_id
    case 'label':
      return a.label === (b as typeof a).label
    case 'region':
      return false
    case 'project':
      return (
        a.project_id === (b as typeof a).project_id &&
        a.drawing_ids.length === (b as typeof a).drawing_ids.length &&
        a.drawing_ids.every((id, i) => id === (b as typeof a).drawing_ids[i])
      )
  }
}

function roomsInScope(d: DrawingFile, scope: Scope): RoomPoly[] {
  switch (scope.kind) {
    case 'floor':
    case 'model':
    case 'project':
      return d.rooms
    case 'unit':
      return d.rooms.filter((r) => r.unit === scope.unit)
    case 'room':
      return d.rooms.filter((r) => r.room_id === scope.room_id)
    case 'label':
      return d.rooms.filter((r) => r.name === scope.label)
    case 'region':
      return d.rooms.filter((r) => pointInRect(r.label_at[0], r.label_at[1], scope.rect))
  }
}

function boxesInScope(d: DrawingFile, all: Box[], scope: Scope): Box[] {
  switch (scope.kind) {
    case 'floor':
    case 'model':
    case 'project':
      return all
    case 'unit':
      return all.filter((b) => b.unit === scope.unit)
    case 'room':
      return all.filter((b) => b.room_id === scope.room_id)
    case 'label': {
      const ids = new Set(d.rooms.filter((r) => r.name === scope.label).map((r) => r.room_id))
      return all.filter((b) => b.room_id !== undefined && ids.has(b.room_id))
    }
    case 'region':
      return all.filter((b) => boxCentreInRect(b, scope.rect))
  }
}

/** Unknown room, unit or label → the scope cannot be answered on this drawing. */
function scopeExists(d: DrawingFile, scope: Scope): boolean {
  if (scope.kind === 'unit') return d.units.some((u) => u.id === scope.unit)
  if (scope.kind === 'room') return d.rooms.some((r) => r.room_id === scope.room_id)
  if (scope.kind === 'label') return d.rooms.some((r) => r.name === scope.label)
  return true
}

function goldFor(
  d: DrawingFile,
  lang: Lang,
  answer: { kind: 'count'; subject: Subject } | { kind: 'area' },
  scope: Scope,
  value: number,
): Gold | undefined {
  const row = d.gold.find(
    (g) =>
      g.answer.kind === answer.kind &&
      (answer.kind === 'area' || (g.answer.kind === 'count' && g.answer.subject === answer.subject)) &&
      sameScope(g.scope, scope),
  )
  if (!row) return undefined
  const match =
    row.tolerance === 'exact' ? value === row.value : Math.abs(value - row.value) <= 0.02 * Math.abs(row.value)
  return {
    dataset_qid: lang === 'fr' ? row.dataset_qid_fr : row.dataset_qid,
    value: row.value,
    tolerance: row.tolerance,
    match,
  }
}

const crossCheck = (evidence_ids: string[]): TraceNode => ({
  id: 'check',
  parent: 'q',
  title: 'Cross-check on the image',
  tag: 'Decisions API · sample',
  body: 'Sample only: the image cross-check runs once the Decisions API is wired. No model has checked this answer.',
  kind: 'model',
  confidence: 'sample',
  evidence_ids,
})

const pickSource: TraceNode = {
  id: 'source',
  parent: 'q',
  title: 'Pick the best source',
  tag: 'router',
  body: 'The CAD geometry bundled with this drawing answers counts and areas exactly, so no image model is needed.',
  kind: 'exact',
}

function questionNode(question: string, lang: Lang): TraceNode {
  return { id: 'q', title: 'Question', tag: `${LANG_NAME[lang]} · detected`, body: `“${question}”`, kind: 'info' }
}

function intentNode(body: string): TraceNode {
  return { id: 'intent', parent: 'q', title: 'Intent', tag: 'local router', body, kind: 'info' }
}

const WHERE_KEY: Record<NotOnDrawingTopic, StringKey> = {
  fire: 'where_fire',
  height: 'where_height',
  material: 'where_material',
}

const TOPIC_EN: Record<NotOnDrawingTopic, { canonical: string; body: string }> = {
  fire: { canonical: 'What is the fire rating', body: 'No layer or label on this drawing carries a fire rating.' },
  height: { canonical: 'What is the room height', body: 'A floor plan has no heights; no label on it states one.' },
  material: {
    canonical: 'What materials are used',
    body: 'No layer or label on this drawing names a material.',
  },
}

/**
 * Qualifiers, back-references and comparisons the offline solver cannot honour (mirrors the server's QUALIFIER_GUARD and
 * ANAPHORA in backend/aura/router.py). Such a question must abstain, never fall back to the unqualified count.
 */
const NEEDS_SERVER = new RegExp(
  String.raw`(?<![\p{L}\p{N}_-])(?:only|just|double|doubles|single|sliding|pocket|folding|revolving|hinged|french doors?|` +
    String.raw`fire (?:doors?|rated)|exterior|external|outer|interior|internal|inner|accessible|wheelchair|` +
    String.raw`in (?:the )?rooms? (?:that|with|having)|leading to|leads? to|lead to|with|without|having|that|those|these|them|it|its|same|` +
    String.raw`bigger|larger|smaller|than|biggest|largest|smallest|widest|narrowest|most|least|more|less|compare\w*|versus|vs|` +
    String.raw`seulement|uniquement|avec|sans|nur|mit|ohne|doppel\w*|vain|ilman|kaksois\w*)(?![\p{L}\p{N}_-])|` +
    String.raw`\d+(?:[.,]\d+)?\s*(?:m2|m²|m³|cm|mm|m)(?![\p{L}\p{N}])`,
  'iu',
)

/** True when the offline solver would have to drop part of the question to answer it. */
export const needsServer = (question: string): boolean => NEEDS_SERVER.test(question)

/** Answer a question about the bundled drawing, entirely in the browser. Throws for any other drawing id. */
export function ask(req: AskRequest, drawing: DrawingFile = bundledDrawing): AskResponse {
  if (req.drawing_id !== drawing.id) throw new Error(`Local answers exist only for ${drawing.id}`)
  const t0 = now()
  const question = req.question.trim()
  const lang: Lang = hasLangSignal(question) ? detect(question) : (req.lang_hint ?? 'en')
  const r = route(question)
  const scope: Scope = req.scope ?? (r.intent !== 'abstain' && r.scope ? r.scope : { kind: 'floor' })
  const overrides = req.overrides ?? []

  const base = {
    contract_version: CONTRACT_VERSION,
    detected_lang: lang,
    scope,
    calls: 0,
    cost_usd: 0,
    source: 'local' as const,
  }
  const finish = (resp: Omit<AskResponse, 'latency_ms' | keyof typeof base>): AskResponse => ({
    ...base,
    ...resp,
    latency_ms: Math.max(0, Math.round(now() - t0)),
  })

  const abstain = (reason: AbstainReason, canonical_en: string, lookBody: string, where_to_find?: string) =>
    finish({
      answer: where_to_find ? { kind: 'abstain', reason, where_to_find } : { kind: 'abstain', reason },
      evidence: { boxes: [], rooms: [] },
      canonical_en,
      method: 'none',
      trace: [
        questionNode(question, lang),
        intentNode(`abstain · ${reason.replace(/_/g, ' ')}`),
        { id: 'look', parent: 'q', title: 'Look for the data', tag: 'drawing', body: lookBody, kind: 'warn' },
        { id: 'answer', parent: 'q', title: 'Answer', tag: 'abstain', body: 'Abstained. Nothing guessed.', kind: 'warn' },
      ],
    })

  if (needsServer(question))
    return abstain(
      'not_supported_yet',
      '(needs the AURA server)',
      'Offline, only plain counts and areas of the demo drawing are answered; nothing was guessed.',
      t(lang, 'offline_needs_server'),
    )

  if (r.intent === 'abstain') {
    if (r.reason === 'not_on_drawing' && r.topic) {
      const topic = TOPIC_EN[r.topic]
      return abstain('not_on_drawing', topic.canonical, topic.body, t(lang, WHERE_KEY[r.topic]))
    }
    return abstain(
      'unclear_question',
      '(no match)',
      'The question matched nothing this drawing answers: rooms, areas, doors, windows or washbasins.',
    )
  }

  if (!scopeExists(drawing, scope))
    return abstain(
      'unclear_question',
      '(no match)',
      `This drawing has no ${scopeText(scope)}; it has rooms ${drawing.rooms[0]?.num}–${drawing.rooms[drawing.rooms.length - 1]?.num} in apartments ${drawing.units.map((u) => u.id).join(' and ')}.`,
    )

  // ---- counts ------------------------------------------------------------
  if (r.intent === 'count') {
    const subject = r.subject
    // Fixture layers exist only in drawing files that carry them; otherwise nothing is guessed.
    if ((subject === 'wc' || subject === 'bathtub' || subject === 'shower') && !drawing.evidence[subject]) {
      const plural = noun(subject, 2, 'en')
      return abstain(
        'not_supported_yet',
        `How many ${plural} are drawn ${whereEn(scope)}`,
        `No ${plural} are extracted from this drawing yet, so there is nothing to count.`,
      )
    }

    if (isBoxSubject(subject)) {
      const layer = drawing.evidence[subject] ?? []
      const boxes = boxesInScope(drawing, layer, scope)
      const plural = noun(subject, 2, 'en')
      const doubles = layer.filter((b) => b.duplicate_of).length
      const readBody =
        `${layer.length} ${noun(subject, layer.length, 'en')} on the DXF ${subject} layer.` +
        (subject === 'door' && doubles > 0
          ? ` ${doubles} doors are drawn twice at the same spot in the CAD file; the dataset counts both copies, so both are counted.`
          : '')
      const trace: TraceNode[] = [
        questionNode(question, lang),
        intentNode(`count · ${plural} · ${scopeText(scope)}`),
        pickSource,
        {
          id: 'read',
          parent: 'source',
          title: 'Read the layer',
          tag: `DXF · ${subject} layer`,
          body: readBody,
          kind: 'exact',
          evidence_ids: boxes.map((b) => b.id),
        },
      ]
      if (scope.kind !== 'floor')
        trace.push({
          id: 'scope',
          parent: 'source',
          title: 'Apply the scope',
          tag: 'geometry',
          body:
            scope.kind === 'region'
              ? `Kept the ones whose centre is inside ${scope.label} → ${boxes.length}.`
              : `Kept the ones ${whereEn(scope)} → ${boxes.length}.`,
          kind: 'exact',
          evidence_ids: boxes.map((b) => b.id),
        })
      trace.push(crossCheck(boxes.map((b) => b.id)), {
        id: 'answer',
        parent: 'q',
        title: 'Answer',
        tag: 'count',
        body: `${boxes.length} ${noun(subject, boxes.length, 'en')}. Each one is drawn on the plan for you to check.`,
        kind: 'exact',
        evidence_ids: boxes.map((b) => b.id),
      })
      return finish({
        answer: { kind: 'count', value: boxes.length, subject },
        evidence: { boxes, rooms: [] },
        canonical_en: `How many ${plural} are drawn ${whereEn(scope)}`,
        method: 'cad_geometry',
        check: { method: 'decisions_api', status: 'sample' },
        gold: overrides.length || scope.kind === 'region' ? undefined : goldFor(drawing, lang, { kind: 'count', subject }, scope, boxes.length),
        trace,
      })
    }

    // rooms and apartments: evidence is room polygons
    const inScope = roomsInScope(drawing, scope)
    const explicit = scope.kind === 'label' || scope.kind === 'room'
    const rooms: EvidenceRoom[] = inScope.map((rm) => ({
      room_id: rm.room_id,
      included: subject === 'apartment' || explicit || !rm.outdoor,
      area_m2: rm.area_m2,
      ...(subject === 'apartment' && rm.unit ? { group_id: `unit-${rm.unit}` } : {}),
    }))
    const ids = rooms.map((x) => roomEvidenceId(x.room_id))
    const includedIds = rooms.filter((x) => x.included).map((x) => roomEvidenceId(x.room_id))
    let value: number
    let canonical: string
    let collectBody: string
    let groups: EvidenceGroup[] | undefined
    if (subject === 'apartment') {
      const units = [...new Set(inScope.map((rm) => rm.unit).filter((u): u is string => Boolean(u)))]
      value = units.length
      groups = units.map((u, i) => ({
        id: `unit-${u}`,
        label: `Apartment ${u}`,
        color: GROUP_COLORS[i % GROUP_COLORS.length],
      }))
      canonical = `How many apartments are ${scope.kind === 'floor' ? 'on this floor plan' : whereEn(scope)}`
      collectBody = `${inScope.length} numbered rooms; each carries an apartment letter: ${units.join(', ')}.`
    } else {
      value = rooms.filter((x) => x.included).length
      canonical =
        scope.kind === 'label'
          ? `How many rooms are labelled ${scope.label}`
          : `How many numbered rooms are ${whereEn(scope)}`
      const outdoor = inScope.filter((rm) => rm.outdoor).length
      collectBody =
        scope.kind === 'label'
          ? `${drawing.rooms.length} numbered rooms on the drawing; ${inScope.length} are labelled ${scope.label}.`
          : `${inScope.length} numbered rooms ${whereEn(scope)}` +
            (outdoor && !explicit ? `; ${outdoor} outdoor left out.` : '.')
    }
    return finish({
      answer: { kind: 'count', value, subject },
      evidence: { boxes: [], rooms, ...(groups && groups.length > 0 ? { groups } : {}) },
      canonical_en: canonical,
      method: 'cad_geometry',
      check: { method: 'decisions_api', status: 'sample' },
      gold: overrides.length || scope.kind === 'region' ? undefined : goldFor(drawing, lang, { kind: 'count', subject }, scope, value),
      trace: [
        questionNode(question, lang),
        intentNode(`count · ${noun(subject, 2, 'en')} · ${scopeText(scope)}`),
        pickSource,
        {
          id: 'collect',
          parent: 'source',
          title: 'Collect rooms',
          tag: 'room labels',
          body: collectBody,
          kind: 'exact',
          evidence_ids: ids,
        },
        crossCheck(includedIds),
        {
          id: 'answer',
          parent: 'q',
          title: 'Answer',
          tag: 'count',
          body: `${value} ${noun(subject, value, 'en')}. Each room is lit on the plan for you to check.`,
          kind: 'exact',
          evidence_ids: includedIds,
        },
      ],
    })
  }

  // ---- areas -------------------------------------------------------------
  const inScope = roomsInScope(drawing, scope)
  const explicit = scope.kind === 'label' || scope.kind === 'room'
  const edits: string[] = []
  const rooms: EvidenceRoom[] = inScope.map((rm) => {
    const o = overrides.find((x) => x.room_id === rm.room_id)
    // A room with no known area is listed but never summed.
    const included = (explicit || !rm.outdoor) && (rm.area_m2 !== null || !!o)
    if (!o) return { room_id: rm.room_id, included, area_m2: rm.area_m2 }
    const area = round2(o.w_m * o.h_m)
    edits.push(
      `Room ${rm.room_id} uses your what-if size ${o.w_m} × ${o.h_m} m = ${area.toFixed(2)} m² (printed ${rm.area_m2 === null ? 'no area' : `${rm.area_m2.toFixed(2)} m²`}).`,
    )
    return { room_id: rm.room_id, included, area_m2: area, edited: true }
  })
  const included = rooms.filter((x) => x.included)
  const value = round2(included.reduce((s, x) => s + (x.area_m2 ?? 0), 0))
  const outdoorLeftOut = rooms.length - included.length
  const ids = rooms.map((x) => roomEvidenceId(x.room_id))
  const includedIds = included.map((x) => roomEvidenceId(x.room_id))
  const usedOverrides = overrides.filter((o) => inScope.some((rm) => rm.room_id === o.room_id))

  let canonical =
    scope.kind === 'floor'
      ? 'What is the net floor area of all numbered indoor rooms'
      : scope.kind === 'label'
        ? `What is the total area of rooms labelled ${scope.label}`
        : scope.kind === 'region'
          ? `What is the net floor area inside ${scope.label}`
          : `What is the net floor area of ${scopeText(scope)}`
  if (usedOverrides.length)
    canonical += ', with ' + usedOverrides.map((o) => `room ${o.room_id} at ${o.w_m} × ${o.h_m} m`).join(' and ')

  const trace: TraceNode[] = [
    questionNode(question, lang),
    intentNode(
      `area · net · ${scopeText(scope)}` +
        (outdoorLeftOut ? ' · without outdoor' : '') +
        usedOverrides.map((o) => ` · what-if ${o.room_id} = ${round2(o.w_m * o.h_m).toFixed(2)} m²`).join(''),
    ),
    pickSource,
    {
      id: 'collect',
      parent: 'source',
      title: 'Collect rooms',
      tag: 'room labels · printed areas',
      body:
        scope.kind === 'room'
          ? `Room ${scope.room_id} (${inScope[0]?.name}), printed net area ${inScope[0]?.area_m2 == null ? 'not printed' : `${inScope[0].area_m2.toFixed(2)} m²`}.`
          : `${rooms.length} numbered ${rooms.length === 1 ? 'room' : 'rooms'} ${whereEn(scope)}` +
            (outdoorLeftOut ? `; ${outdoorLeftOut} outdoor left out.` : '.'),
      kind: 'exact',
      evidence_ids: ids,
    },
  ]
  if (edits.length)
    trace.push({
      id: 'whatif',
      parent: 'source',
      title: 'Apply your what-if size',
      tag: 'what-if',
      body: edits.join(' '),
      kind: 'info',
      evidence_ids: rooms.filter((x) => x.edited).map((x) => roomEvidenceId(x.room_id)),
    })
  trace.push(crossCheck(includedIds), {
    id: 'answer',
    parent: 'q',
    title: 'Answer',
    tag: 'sum',
    body:
      included.length === 1
        ? `${value.toFixed(2)} m², shown on the plan.`
        : `Sum of ${included.length} net room areas = ${value.toFixed(2)} m², shown room by room.`,
    kind: 'exact',
    evidence_ids: includedIds,
  })

  return finish({
    answer: { kind: 'area', value, unit: 'm2' },
    evidence: { boxes: [], rooms },
    canonical_en: canonical,
    method: 'cad_geometry',
    check: { method: 'decisions_api', status: 'sample' },
    gold: overrides.length || scope.kind === 'region' ? undefined : goldFor(drawing, lang, { kind: 'area' }, scope, value),
    trace,
  })
}

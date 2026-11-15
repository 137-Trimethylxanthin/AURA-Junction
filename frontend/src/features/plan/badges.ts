import type {
  Box,
  EvidenceGroup,
  EvidenceLink,
  EvidenceRoom,
  GroupColor,
  Lang,
  PctRect,
  ProjectBundle,
  ProjectFloor,
} from '@/api/contract'
import { myProjects } from '@/api/library'
import { actions, type AppState, type StreamState } from '@/store'
import { bboxOf, clampRect } from '@/lib/geometry'

/** Badge diameter plus a 2 px gap, in px. */
export const BADGE = 26

export interface BadgeAt {
  /** Anchor in % of the stage. */
  x: number
  y: number
  /** Offset of the badge centre from the anchor, in px. */
  dx: number
  dy: number
}

/**
 * Objects drawn twice: only the explicit `duplicate_of` signal (contract 1.1), the same rule as the card rows.
 * The box it points at is leaf 1, the box carrying it is leaf 2; a pointer to a box not in the list is ignored.
 */
export function doubleLeaves(boxes: readonly Box[]): { firstLeaf: Set<string>; secondLeaf: Set<string> } {
  const ids = new Set(boxes.map((b) => b.id))
  const firstLeaf = new Set<string>()
  const secondLeaf = new Set<string>()
  for (const b of boxes) {
    if (!b.duplicate_of || !ids.has(b.duplicate_of)) continue
    firstLeaf.add(b.duplicate_of)
    secondLeaf.add(b.id)
  }
  return { firstLeaf, secondLeaf }
}

const SIDE = BADGE / 2 + 1

/**
 * Deterministic badge placement, in reading order: try the box's corners (double-door leaves try
 * just outside their own side) and take the first spot that doesn't overlap a badge already placed;
 * otherwise nudge the first candidate outwards; otherwise keep the first candidate.
 */
export function placeBadges(
  boxes: readonly Box[],
  stageW: number,
  stageH: number,
  firstLeaf: ReadonlySet<string>,
  secondLeaf: ReadonlySet<string>,
): Map<string, BadgeAt> {
  const out = new Map<string, BadgeAt>()
  const placed: [number, number][] = []
  const px = (a: BadgeAt): [number, number] => [(a.x / 100) * stageW + a.dx, (a.y / 100) * stageH + a.dy]
  const free = (a: BadgeAt) => {
    const [cx, cy] = px(a)
    return placed.every(([x, y]) => Math.abs(x - cx) >= BADGE || Math.abs(y - cy) >= BADGE)
  }

  for (const b of boxes) {
    const r = b.x + b.w
    const btm = b.y + b.h
    const cands: BadgeAt[] = firstLeaf.has(b.id)
      ? [
          { x: b.x, y: b.y, dx: -SIDE, dy: 0 },
          { x: b.x, y: btm, dx: -SIDE, dy: 0 },
        ]
      : secondLeaf.has(b.id)
        ? [
            { x: r, y: b.y, dx: SIDE, dy: 0 },
            { x: r, y: btm, dx: SIDE, dy: 0 },
          ]
        : [
            { x: b.x, y: b.y, dx: 0, dy: 0 },
            { x: r, y: b.y, dx: 0, dy: 0 },
            { x: b.x, y: btm, dx: 0, dy: 0 },
            { x: r, y: btm, dx: 0, dy: 0 },
          ]
    const first = cands[0]
    const nudges: [number, number][] = [
      [0, -BADGE],
      [-BADGE, 0],
      [BADGE, 0],
      [0, BADGE],
      [-BADGE, -BADGE],
      [BADGE, -BADGE],
    ]
    const pick =
      cands.find(free) ??
      nudges.map(([dx, dy]) => ({ ...first, dx: first.dx + dx, dy: first.dy + dy })).find(free) ??
      first
    out.set(b.id, pick)
    placed.push(px(pick))
  }
  return out
}

// ---- categorical groups, links & multi-floor bundle types -------------------

export type { GroupColor }
export type EvidenceGroupLike = EvidenceGroup
export type EvidenceLinkLike = EvidenceLink
export type GroupedBox = Box
export type GroupedRoom = EvidenceRoom
export type ProjectFloorLike = ProjectFloor
export type ProjectBundleLike = ProjectBundle

export const GROUP_VARS: Record<GroupColor, { main: string; surface: string }> = {
  c1: { main: 'var(--group-1)', surface: 'var(--group-1-surface)' },
  c2: { main: 'var(--group-2)', surface: 'var(--group-2-surface)' },
  c3: { main: 'var(--group-3)', surface: 'var(--group-3-surface)' },
  c4: { main: 'var(--group-4)', surface: 'var(--group-4-surface)' },
  c5: { main: 'var(--group-5)', surface: 'var(--group-5-surface)' },
  c6: { main: 'var(--group-6)', surface: 'var(--group-6-surface)' },
}

const GROUP_CYCLE: readonly GroupColor[] = ['c1', 'c2', 'c3', 'c4', 'c5', 'c6']

export function groupVarsOf(color: string | undefined, fallbackIdx = 0): { main: string; surface: string } {
  if (color && color in GROUP_VARS) {
    return GROUP_VARS[color as GroupColor]
  }
  const c = GROUP_CYCLE[((fallbackIdx % GROUP_CYCLE.length) + GROUP_CYCLE.length) % GROUP_CYCLE.length]
  return GROUP_VARS[c]
}

export function resolveGroupStyle(
  groupId: string | undefined,
  groups: readonly EvidenceGroupLike[] | undefined,
): { main: string; surface: string; group: EvidenceGroupLike } | null {
  if (!groupId || !groups || groups.length === 0) return null
  const idx = groups.findIndex((g) => g.id === groupId)
  if (idx === -1) return null
  const group = groups[idx]
  const vars = groupVarsOf(group.color, idx)
  return { ...vars, group }
}

// ---- #spot=x,y deep-link parser ---------------------------------------------

/** Parse `#spot=x,y` (or `#...&spot=x,y`) in `%` coordinates `[0, 100]`. Returns null when invalid or absent. */
export function parseSpotHash(hash: string): [number, number] | null {
  if (!hash) return null
  const clean = hash.startsWith('#') ? hash.slice(1) : hash
  for (const part of clean.split('&')) {
    const m = /^spot=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/i.exec(part.trim())
    if (!m) continue
    const x = Number(m[1])
    const y = Number(m[2])
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null
    if (x < 0 || x > 100 || y < 0 || y > 100) return null
    return [x, y]
  }
  return null
}

// ---- live agent telemetry & box IoU diffing ---------------------------------

export function rectsIntersect(a: PctRect, b: PctRect): boolean {
  const ax1 = a.x + Math.max(0, a.w)
  const ay1 = a.y + Math.max(0, a.h)
  const bx1 = b.x + Math.max(0, b.w)
  const by1 = b.y + Math.max(0, b.h)
  return a.x <= bx1 && ax1 >= b.x && a.y <= by1 && ay1 >= b.y
}

export function boxIoU(a: PctRect, b: PctRect): number {
  const aw = Math.max(0, a.w)
  const ah = Math.max(0, a.h)
  const bw = Math.max(0, b.w)
  const bh = Math.max(0, b.h)
  const ix0 = Math.max(a.x, b.x)
  const iy0 = Math.max(a.y, b.y)
  const ix1 = Math.min(a.x + aw, b.x + bw)
  const iy1 = Math.min(a.y + ah, b.y + bh)
  const iw = Math.max(0, ix1 - ix0)
  const ih = Math.max(0, iy1 - iy0)
  const inter = iw * ih
  const union = aw * ah + bw * bh - inter
  if (union <= 0) {
    return Math.abs(a.x - b.x) < 1e-3 && Math.abs(a.y - b.y) < 1e-3 ? 1 : 0
  }
  return inter / union
}

/**
 * Candidates seen during live streaming that were eliminated from the final answer on `drawingId`
 * (matched by exact `id` or `boxIoU >= 0.5`).
 */
export function diffDroppedBoxes(
  streamBoxes: readonly GroupedBox[],
  finalBoxes: readonly GroupedBox[],
  drawingId: string,
): GroupedBox[] {
  const onFloor = (b: GroupedBox) => !b.drawing_id || b.drawing_id === drawingId
  const candidates = streamBoxes.filter(onFloor)
  const confirmed = finalBoxes.filter(onFloor)
  if (candidates.length === 0) return []
  const confirmedIds = new Set(confirmed.map((b) => b.id))
  return candidates.filter((cand) => {
    if (confirmedIds.has(cand.id)) return false
    return !confirmed.some((fb) => boxIoU(cand, fb) >= 0.5)
  })
}

function asPctRect(v: unknown): PctRect | null {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return null
  const o = v as Record<string, unknown>
  if (
    typeof o.x === 'number' &&
    Number.isFinite(o.x) &&
    typeof o.y === 'number' &&
    Number.isFinite(o.y) &&
    typeof o.w === 'number' &&
    Number.isFinite(o.w) &&
    typeof o.h === 'number' &&
    Number.isFinite(o.h)
  ) {
    return clampRect({ x: o.x, y: o.y, w: o.w, h: o.h })
  }
  return null
}

function asCropPctRect(cropPx: unknown, imagePx: unknown): PctRect | null {
  if (!Array.isArray(cropPx) || cropPx.length < 4 || !Array.isArray(imagePx) || imagePx.length < 2) return null
  const [x0, y0, x1, y1] = cropPx
  const [imgW, imgH] = imagePx
  if (
    typeof x0 !== 'number' ||
    typeof y0 !== 'number' ||
    typeof x1 !== 'number' ||
    typeof y1 !== 'number' ||
    typeof imgW !== 'number' ||
    typeof imgH !== 'number' ||
    !Number.isFinite(x0) ||
    !Number.isFinite(y0) ||
    !Number.isFinite(x1) ||
    !Number.isFinite(y1) ||
    !Number.isFinite(imgW) ||
    !Number.isFinite(imgH) ||
    imgW <= 0 ||
    imgH <= 0
  ) {
    return null
  }
  const left = Math.min(x0, x1)
  const top = Math.min(y0, y1)
  const width = Math.abs(x1 - x0)
  const height = Math.abs(y1 - y0)
  return clampRect({
    x: (left / imgW) * 100,
    y: (top / imgH) * 100,
    w: (width / imgW) * 100,
    h: (height / imgH) * 100,
  })
}

const MACHINE_STEP_SLUGS = new Set([
  'view_image',
  'shell',
  'scan-candidates',
  'scan_candidates',
  'crop',
  'deep_agent',
  'count_objects',
  'room_areas',
  'list_rooms',
  'locate',
  'scope_to_region',
  'compare_with_original',
  'check_boxes_with_decisions',
  'what_if',
  'find_text',
])

function isMachineStepSlug(s: string): boolean {
  return MACHINE_STEP_SLUGS.has(s.toLowerCase())
}

function asStringList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && x.length > 0) : []
}

export interface AgentTelemetry {
  stepIndex: number
  stepLabel: string
  queuePosition?: number
  focusRect: PctRect | null
  droppedIds: Set<string>
  keptIds: Set<string>
  markedCount: number
  droppedCount: number
}

export function extractAgentTelemetry(stream: StreamState | undefined, drawingId: string, lang: Lang = 'en'): AgentTelemetry {
  const droppedIds = new Set<string>()
  const keptIds = new Set<string>()
  if (!stream) {
    return {
      stepIndex: 0,
      stepLabel: '',
      focusRect: null,
      droppedIds,
      keptIds,
      markedCount: 0,
      droppedCount: 0,
    }
  }

  const floorBoxes = (stream.evidence.boxes as GroupedBox[]).filter((b) => !b.drawing_id || b.drawing_id === drawingId)
  const boxById = new Map(floorBoxes.map((b) => [b.id, b]))

  let focusRect: PctRect | null = null
  let stepIndex = 0
  let stepLabel = ''

  for (const node of stream.nodes) {
    const args = node.tool?.args ?? {}
    const nodeDrawing = typeof args.drawing_id === 'string' ? args.drawing_id : undefined
    if (node.tool || node.kind === 'model') {
      stepIndex = typeof args.seq === 'number' && Number.isFinite(args.seq) && args.seq > 0 ? Math.round(args.seq) : stepIndex + 1
    }
    for (const id of asStringList(args.dropped_ids)) {
      droppedIds.add(id)
      keptIds.delete(id)
    }
    for (const id of asStringList(args.kept_ids)) {
      keptIds.add(id)
      droppedIds.delete(id)
    }
    if (!nodeDrawing || nodeDrawing === drawingId) {
      const explicitRect =
        asPctRect(args.focus_rect ?? args.crop_rect ?? args.rect) ?? asCropPctRect(args.crop_px, args.image_px)
      if (explicitRect && (explicitRect.w > 0 || explicitRect.h > 0)) {
        focusRect = explicitRect
      } else if (node.evidence_ids && node.evidence_ids.length > 0) {
        const matched = node.evidence_ids.map((id) => boxById.get(id)).filter((b): b is GroupedBox => Boolean(b))
        if (matched.length > 0) {
          const pts: [number, number][] = matched.flatMap((b) => [
            [b.x, b.y],
            [b.x + b.w, b.y + b.h],
          ])
          const bb = bboxOf(pts)
          focusRect = clampRect({ x: bb.x - 2, y: bb.y - 2, w: Math.max(6, bb.w + 4), h: Math.max(6, bb.h + 4) })
        }
      }
    }
    const argStep = typeof args.step === 'string' ? args.step.trim() : ''
    const titleStep = node.title?.trim() ?? ''
    if (argStep && !isMachineStepSlug(argStep)) {
      stepLabel = argStep
    } else if (titleStep && !isMachineStepSlug(titleStep)) {
      stepLabel = titleStep
    } else if (argStep) {
      stepLabel = STAGE_COPY[lang].toolStep(argStep.toLowerCase())
    } else if (titleStep) {
      stepLabel = STAGE_COPY[lang].toolStep(titleStep.toLowerCase())
    }
  }

  if (!stepLabel) {
    stepLabel = STAGE_COPY[lang].phase(stream.phase)
  }

  const markedCount = floorBoxes.length
  let droppedOnFloor = 0
  for (const b of floorBoxes) {
    if (droppedIds.has(b.id)) droppedOnFloor++
  }
  const droppedCount = Math.max(droppedOnFloor, markedCount === 0 ? droppedIds.size : droppedOnFloor)

  return {
    stepIndex,
    stepLabel,
    queuePosition: stream.queuePosition,
    focusRect,
    droppedIds,
    keptIds,
    markedCount,
    droppedCount,
  }
}

// ---- Wave 0.5 store readers/writers -----------------------------------------

type AnyRecord = Record<string, unknown>

export function readActiveProject(s: AppState): ProjectBundleLike | null {
  const top = (s as unknown as AnyRecord).activeProject
  const ses = (s.session as unknown as AnyRecord).activeProject
  const ui = (s.ui as unknown as AnyRecord).activeProject
  const cand = (top ?? ses ?? ui ?? null) as ProjectBundleLike | null
  if (cand && Array.isArray(cand.floors)) return cand
  return null
}

/**
 * Resolve the active multi-floor `ProjectBundle` for `drawingId` from store state first
 * (verifying membership), falling back to saved `myProjects.forDrawing(drawingId)`.
 */
export function resolveProjectForDrawing(
  storeProject: ProjectBundleLike | null,
  drawingId: string,
): ProjectBundleLike | null {
  if (storeProject && storeProject.floors.some((f) => f && f.drawing_id === drawingId)) {
    return storeProject
  }
  return myProjects.forDrawing(drawingId)
}

export function readCrossPlanScope(s: AppState): boolean {
  const top = (s as unknown as AnyRecord).crossPlanScope
  const ui = (s.ui as unknown as AnyRecord).crossPlanScope
  const ses = (s.session as unknown as AnyRecord).crossPlanScope
  return Boolean(top ?? ui ?? ses ?? false)
}

export function readFocusedGroupId(s: AppState): string | null {
  const top = (s as unknown as AnyRecord).focusedGroupId
  const ui = (s.ui as unknown as AnyRecord).focusedGroupId
  const v = top ?? ui ?? null
  return typeof v === 'string' ? v : null
}

/**
 * Update `focusedGroupId` in `appStore` via Wave 0.5's `setFocusedGroup` (or legacy `setFocusedGroupId` alias).
 * Returns `true` when handled by the store, `false` when no store setter is present.
 */
export function writeFocusedGroupId(nextId: string | null): boolean {
  const act = actions() as unknown as {
    setFocusedGroup?: (id: string | null) => void
    setFocusedGroupId?: (id: string | null) => void
  }
  const fn = act.setFocusedGroup ?? act.setFocusedGroupId
  if (typeof fn === 'function') {
    fn(nextId)
    return true
  }
  return false
}

// ---- 4-locale stage micro-copy (EN / FR / FI / DE) --------------------------

interface StageCopyEntry {
  phase: (p: StreamState['phase']) => string
  toolStep: (slug: string) => string
  queue: (n: number) => string
  step: (n: number, label: string) => string
  marked: (n: number) => string
  dropped: (n: number) => string
  showFilteredOut: (n: number) => string
  hideFilteredOut: (n: number) => string
  allFloors: (n: number) => string
  floorSwitcherAria: string
  groupLegendAria: string
  jumpToFloor: (label: string) => string
}

export const STAGE_COPY: Record<Lang, StageCopyEntry> = {
  en: {
    phase: (p) =>
      p === 'checking'
        ? 'Checking boxes'
        : p === 'working'
          ? 'Inspecting drawing'
          : p === 'understood'
            ? 'Scanning candidates'
            : 'Reading question',
    toolStep: (slug) =>
      slug === 'view_image' || slug === 'crop'
        ? 'Inspecting crop'
        : slug === 'scan-candidates' || slug === 'scan_candidates'
          ? 'Scanning candidates'
          : slug === 'check_boxes_with_decisions'
            ? 'Checking boxes'
            : 'Inspecting drawing',
    queue: (n) => `In queue (#${n})…`,
    step: (n, label) => (n > 0 ? `Step ${n} · ${label}` : label),
    marked: (n) => `${n} marked`,
    dropped: (n) => `${n} dropped`,
    showFilteredOut: (n) => `Show ${n} filtered out`,
    hideFilteredOut: (n) => `Hide ${n} filtered out`,
    allFloors: (n) => `All ${n} floors`,
    floorSwitcherAria: 'Building floors',
    groupLegendAria: 'Color-coded groups',
    jumpToFloor: (label) => `Open ${label}`,
  },
  fr: {
    phase: (p) =>
      p === 'checking'
        ? 'Contrôle des zones'
        : p === 'working'
          ? 'Inspection du plan'
          : p === 'understood'
            ? 'Repérage des candidats'
            : 'Lecture de la question',
    toolStep: (slug) =>
      slug === 'view_image' || slug === 'crop'
        ? 'Inspection d’un extrait'
        : slug === 'scan-candidates' || slug === 'scan_candidates'
          ? 'Repérage des candidats'
          : slug === 'check_boxes_with_decisions'
            ? 'Contrôle des zones'
            : 'Inspection du plan',
    queue: (n) => `En file d’attente (n° ${n})…`,
    step: (n, label) => (n > 0 ? `Étape ${n} · ${label}` : label),
    marked: (n) => `${n} marqués`,
    dropped: (n) => `${n} écartés`,
    showFilteredOut: (n) => `Afficher ${n} écartés`,
    hideFilteredOut: (n) => `Masquer ${n} écartés`,
    allFloors: (n) => `Les ${n} étages`,
    floorSwitcherAria: 'Étages du bâtiment',
    groupLegendAria: 'Groupes colorés',
    jumpToFloor: (label) => `Ouvrir ${label}`,
  },
  fi: {
    phase: (p) =>
      p === 'checking'
        ? 'Tarkistetaan kohteita'
        : p === 'working'
          ? 'Tutkitaan piirustusta'
          : p === 'understood'
            ? 'Etsitään ehdokkaita'
            : 'Luetaan kysymystä',
    toolStep: (slug) =>
      slug === 'view_image' || slug === 'crop'
        ? 'Tutkitaan osa-aluetta'
        : slug === 'scan-candidates' || slug === 'scan_candidates'
          ? 'Etsitään ehdokkaita'
          : slug === 'check_boxes_with_decisions'
            ? 'Tarkistetaan kohteita'
            : 'Tutkitaan piirustusta',
    queue: (n) => `Jonossa (#${n})…`,
    step: (n, label) => (n > 0 ? `Vaihe ${n} · ${label}` : label),
    marked: (n) => `${n} merkitty`,
    dropped: (n) => `${n} karsittu`,
    showFilteredOut: (n) => `Näytä ${n} karsittua`,
    hideFilteredOut: (n) => `Piilota ${n} karsittua`,
    allFloors: (n) => `Kaikki ${n} kerrosta`,
    floorSwitcherAria: 'Rakennuksen kerrokset',
    groupLegendAria: 'Värikoodatut ryhmät',
    jumpToFloor: (label) => `Avaa ${label}`,
  },
  de: {
    phase: (p) =>
      p === 'checking'
        ? 'Objekte werden geprüft'
        : p === 'working'
          ? 'Plan wird untersucht'
          : p === 'understood'
            ? 'Kandidaten werden markiert'
            : 'Frage wird gelesen',
    toolStep: (slug) =>
      slug === 'view_image' || slug === 'crop'
        ? 'Ausschnitt wird geprüft'
        : slug === 'scan-candidates' || slug === 'scan_candidates'
          ? 'Kandidaten werden markiert'
          : slug === 'check_boxes_with_decisions'
            ? 'Objekte werden geprüft'
            : 'Plan wird untersucht',
    queue: (n) => `In Warteschlange (Nr. ${n})…`,
    step: (n, label) => (n > 0 ? `Schritt ${n} · ${label}` : label),
    marked: (n) => `${n} markiert`,
    dropped: (n) => `${n} verworfen`,
    showFilteredOut: (n) => `${n} verworfene zeigen`,
    hideFilteredOut: (n) => `${n} verworfene ausblenden`,
    allFloors: (n) => `Alle ${n} Geschosse`,
    floorSwitcherAria: 'Gebäudegeschosse',
    groupLegendAria: 'Farbcodierte Gruppen',
    jumpToFloor: (label) => `${label} öffnen`,
  },
  it: {
    phase: (p) =>
      p === 'checking'
        ? 'Verifica dei riquadri'
        : p === 'working'
          ? 'Esame del disegno'
          : p === 'understood'
            ? 'Ricerca dei candidati'
            : 'Lettura della domanda',
    toolStep: (slug) =>
      slug === 'view_image' || slug === 'crop'
        ? 'Esame del ritaglio'
        : slug === 'scan-candidates' || slug === 'scan_candidates'
          ? 'Ricerca dei candidati'
          : slug === 'check_boxes_with_decisions'
            ? 'Verifica dei riquadri'
            : 'Esame del disegno',
    queue: (n) => `In coda (n. ${n})…`,
    step: (n, label) => (n > 0 ? `Passo ${n} · ${label}` : label),
    marked: (n) => `${n} segnati`,
    dropped: (n) => `${n} esclusi`,
    showFilteredOut: (n) => `Mostra ${n} esclusi`,
    hideFilteredOut: (n) => `Nascondi ${n} esclusi`,
    allFloors: (n) => `Tutti i ${n} piani`,
    floorSwitcherAria: "Piani dell'edificio",
    groupLegendAria: 'Gruppi colorati',
    jumpToFloor: (label) => `Apri ${label}`,
  },
}

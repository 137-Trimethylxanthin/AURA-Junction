import type { AskResponse, Drawing, EvidenceRoom, RoomId } from '@/api/contract'

/**
 * The user's per-turn edits, keyed by evidence id.
 * - `rejected[boxId] = true`: the box is not counted.
 * - `included["room-<room_id>"] = bool`: overrides the server's `included` for that room.
 */
export interface Toggles {
  rejected: Record<string, boolean>
  included: Record<string, boolean>
}

export const emptyToggles = (): Toggles => ({ rejected: {}, included: {} })

export const roomEvidenceId = (room_id: RoomId) => `room-${room_id}`
export const isRoomEvidenceId = (id: string) => id.startsWith('room-')
export const roomIdOfEvidence = (id: string): RoomId => id.slice('room-'.length)

export type GoldChipState = 'match' | 'within_tolerance' | 'mismatch' | 'user_changed' | 'none'

export interface GoldChip {
  state: GoldChipState
  dataset?: number
  dataset_qid?: string
}

export interface Derived {
  kind: AskResponse['answer']['kind']
  /** Value to display after the user's toggles; null for an abstain. */
  value: number | null
  /** Value the source returned. */
  original: number | null
  /** True when any toggle differs from the source's evidence. */
  changed: boolean
  /** Box ids counted / rejected (count answers over boxes). */
  counted: string[]
  rejected: string[]
  /** Rooms included after toggles. */
  includedRooms: EvidenceRoom[]
  gold: GoldChip
}

export const round2 = (n: number) => Math.round(n * 100) / 100

export function isRoomIncluded(room: EvidenceRoom, t: Toggles): boolean {
  const o = t.included[roomEvidenceId(room.room_id)]
  return o === undefined ? room.included : o
}

export function isBoxRejected(boxId: string, t: Toggles): boolean {
  return t.rejected[boxId] === true
}

/**
 * Displayed value for a response plus the user's toggles.
 * - count of window/door/washbasin/wc/…: boxes not rejected
 * - count of rooms: rooms whose `included` is true after toggles
 * - count of apartments: distinct units among the included rooms (needs `drawing`; otherwise unchanged)
 * - area: sum of the included rooms' area_m2, rounded to 2 dp
 */
export function deriveAnswer(resp: AskResponse, t: Toggles, drawing?: Drawing): Derived {
  const { answer, evidence } = resp
  const includedRooms = evidence.rooms.filter((r) => isRoomIncluded(r, t))
  const counted = evidence.boxes.filter((b) => !isBoxRejected(b.id, t)).map((b) => b.id)
  const rejected = evidence.boxes.filter((b) => isBoxRejected(b.id, t)).map((b) => b.id)
  const changed = rejected.length > 0 || evidence.rooms.some((r) => isRoomIncluded(r, t) !== r.included)

  let value: number | null = null
  let original: number | null = null
  if (answer.kind === 'count') {
    original = answer.value
    if (!changed) value = answer.value
    else if (answer.subject === 'room') value = includedRooms.length
    else if (answer.subject === 'apartment') {
      if (drawing) {
        const unitOf = new Map(drawing.rooms.map((r) => [r.room_id, r.unit]))
        value = new Set(includedRooms.map((r) => unitOf.get(r.room_id)).filter(Boolean)).size
      } else value = answer.value
    } else value = counted.length
  } else if (answer.kind === 'quantity') {
    // Measured quantities (IFC) are shown as given; nothing here can recompute them.
    original = answer.value
    value = answer.value
  } else if (answer.kind === 'area') {
    original = answer.value
    value = changed ? round2(includedRooms.reduce((s, r) => s + r.area_m2, 0)) : answer.value
  }

  return { kind: answer.kind, value, original, changed, counted, rejected, includedRooms, gold: goldChip(resp, changed) }
}

export function goldChip(resp: AskResponse, changed: boolean): GoldChip {
  const g = resp.gold
  if (!g || resp.answer.kind === 'abstain' || resp.answer.kind === 'text') return { state: 'none' }
  const base = { dataset: g.value, dataset_qid: g.dataset_qid }
  if (changed) return { state: 'user_changed', ...base }
  if (!g.match) return { state: 'mismatch', ...base }
  return { state: round2(resp.answer.value) === round2(g.value) ? 'match' : 'within_tolerance', ...base }
}

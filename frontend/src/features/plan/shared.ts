import type { RefObject } from 'react'
import type { BoxSubjectAny, Lang, RoomPoly } from '@/api/contract'
import { noun } from '@/lib/format'

/** Rendered drawing aspect (width / height of drawing.images.rebuilt). */
export const PLAN_ASPECT = 1221 / 1220

/** Pointer position in % of the stage element, clamped to 0–100. */
export function pctPoint(stage: HTMLElement, clientX: number, clientY: number): [number, number] {
  const r = stage.getBoundingClientRect()
  const x = ((clientX - r.left) / r.width) * 100
  const y = ((clientY - r.top) / r.height) * 100
  return [Math.min(100, Math.max(0, x)), Math.min(100, Math.max(0, y))]
}

export type StageRef = RefObject<HTMLDivElement | null>

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

/** "Window", "Door" … in the given language, singular. */
export const subjectLabel = (subject: BoxSubjectAny, lang: Lang) => cap(noun(subject, 1, lang))

export const roomTitle = (r: Pick<RoomPoly, 'name' | 'num'>) => `${r.name} ${r.num}`

/** True when an editable element has focus (keyboard shortcuts must not fire there). */
export function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

/** Metres with 2–3 decimals (4.135, 3.60). */
export const fmtMetres = (n: number) =>
  new Intl.NumberFormat('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 3 }).format(n)

/** Parses "3,6" or "3.60"; null when not a positive number. */
export function parseMetres(s: string): number | null {
  const v = Number(s.trim().replace(',', '.'))
  return Number.isFinite(v) && v > 0 ? v : null
}

/** A menu or dialog is open somewhere: it owns Escape. */
export const overlayOpen = () => !!document.querySelector('[role=menu],[role=dialog],[role=alertdialog]')

/** Swipe divider position in % (shared by the compare layer and the context menu without loading the layer). */
export const swipeSplit = { current: 50 }

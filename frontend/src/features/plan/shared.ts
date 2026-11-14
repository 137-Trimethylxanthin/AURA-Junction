import { LOCALE } from '@/lib/format'
import { tu as tuNow, uiLang } from '@/i18n/uiLang'
import type { RefObject } from 'react'
import type { BoxSubjectAny, Lang, RoomPoly } from '@/api/contract'
import { noun } from '@/lib/format'
import { actions } from '@/store'

export const SPLIT_QUERY = '(min-width: 768px)'
export const MOBILE_QUERY = '(max-width: 767px)'

/** Smallest wall we accept in What-If resize, in metres. */
export const MIN_M = 0.5

/** Rendered drawing aspect (width / height of drawing.images.rebuilt). */
export const PLAN_ASPECT = 1221 / 1220

/** Width / height of a drawing's rendered image (uploads are rarely square). */
export const planAspect = (d: { frame: { px: [number, number] } }): number =>
  d.frame.px[0] > 0 && d.frame.px[1] > 0 ? d.frame.px[0] / d.frame.px[1] : PLAN_ASPECT

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
  if (!(target instanceof Element)) return false
  if (target instanceof HTMLElement && target.isContentEditable) return true
  return Boolean(
    target.closest(
      'input, textarea, select, [contenteditable="true"], [contenteditable=""], [role="textbox"], [role="combobox"], [role="searchbox"]',
    ),
  )
}

/** Metres with 2–3 decimals (4.135, 3.60). */
export const fmtMetres = (n: number) =>
  new Intl.NumberFormat(LOCALE[uiLang.get()], { minimumFractionDigits: 2, maximumFractionDigits: 3 }).format(n)

/** Parses "3,6" or "3.60"; null when not a positive number. */
export function parseMetres(s: string): number | null {
  const v = Number(s.trim().replace(',', '.'))
  return Number.isFinite(v) && v > 0 ? v : null
}

/** "Area n": counts only marks drawn with the tool (spots are numbered separately). */
export function nextLabel(kind: 'Area' | 'Spot', marks: readonly { label: string }[] = actions().ui.marks): string {
  const n = marks.filter((m) => m.label.startsWith(`${kind} `)).length + 1
  return `${kind} ${n}`
}

/** "Area 2" / "Spot 1" are stored in English (they are keys and go to the agent); shown in the interface language. */
export function markLabel(label: string, tu: typeof tuNow = tuNow): string {
  const m = /^(Area|Spot) (\d+)$/.exec(label)
  return m ? tu(m[1] === 'Area' ? 'mark_area' : 'mark_spot', { n: m[2] }) : label
}

/** Largest width/depth that keeps the edited room inside the drawing. */
export function limitsOf(room: RoomPoly): { maxW: number; maxH: number } {
  const r = room.rect!
  return {
    maxW: Math.floor(((100 - r.x) / r.w) * r.w_m * 100) / 100,
    maxH: Math.floor(((100 - r.y) / r.h) * r.h_m * 100) / 100,
  }
}

/** A menu, dialog, listbox or non-tooltip popover is open somewhere: it owns Escape and single-key hotkeys. */
export function overlayOpen(): boolean {
  if (typeof document === 'undefined') return false
  if (
    document.querySelector(
      '[role=menu]:not([data-state="closed"]),[role=dialog]:not([data-state="closed"]),[role=alertdialog]:not([data-state="closed"]),[role=listbox]:not([data-state="closed"])',
    )
  ) {
    return true
  }
  const poppers = document.querySelectorAll('[data-radix-popper-content-wrapper]')
  for (const el of poppers) {
    if (el.querySelector('[data-slot="tooltip-content"],[role="tooltip"]')) continue
    if (el.querySelector('[data-state="closed"]')) continue
    return true
  }
  return false
}

/** True when a global workspace shortcut must not fire (IME composition, editable controls, sliders, open overlays). */
export function shouldIgnoreShortcut(e: {
  defaultPrevented?: boolean
  isComposing?: boolean
  keyCode?: number
  target: EventTarget | null
}): boolean {
  if (e.defaultPrevented) return true
  if (e.isComposing || e.keyCode === 229) return true
  if (isEditable(e.target)) return true
  if (e.target instanceof Element && e.target.closest('[role="application"],[role="slider"],[role="spinbutton"]')) {
    return true
  }
  return overlayOpen()
}

/** Swipe divider position in % (shared by the compare layer and the context menu without loading the layer). */
export const swipeSplit = { current: 50 }

/** Stage-local view commands sent by shortcuts / the palette; `PlanStage` listens (it owns scroll, peek and dropped state). */
export const STAGE_COMMAND_EVENT = 'aura:stage-command'
export type StageCommand =
  | { type: 'pan'; dx: number; dy: number }
  | { type: 'fit' }
  | { type: 'peek' }
  | { type: 'dropped' }

export function sendStageCommand(detail: StageCommand): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent<StageCommand>(STAGE_COMMAND_EVENT, { detail }))
}

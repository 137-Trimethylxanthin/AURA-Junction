import { create } from 'zustand'
import type { Drawing, Subject } from '@/api/contract'

/**
 * The composer's text, shared so prompt blocks (plugins sheet), voice and suggestions can fill it.
 * `focusTick` bumps whenever something wants the textarea focused; `select` optionally selects a range.
 */
interface DraftState {
  text: string
  focusTick: number
  select: [number, number] | null
  setText(text: string): void
  fill(text: string, select?: [number, number] | null): void
  focus(): void
}

export const useDraft = create<DraftState>()((set) => ({
  text: '',
  focusTick: 0,
  select: null,
  setText: (text) => set({ text }),
  fill: (text, select = null) => set((s) => ({ text, select, focusTick: s.focusTick + 1 })),
  focus: () => set((s) => ({ focusTick: s.focusTick + 1, select: null })),
}))

/** Prompt blocks: templates the local adapter answers. `select` marks the part worth editing. */
export interface PromptBlock {
  label: string
  text: string
  select?: string
}

export const PROMPT_BLOCKS: PromptBlock[] = [
  { label: 'Count', text: 'How many windows are drawn on this floor plan?', select: 'windows' },
  { label: 'Area', text: 'What is the net floor area of apartment A?', select: 'A' },
  { label: 'Where is', text: 'Where are the washbasins?', select: 'washbasins' },
  { label: 'Check', text: 'Check the door count on this floor plan', select: 'door' },
  { label: 'List', text: 'List every door', select: 'door' },
]

export function fillBlock(b: PromptBlock) {
  const i = b.select ? b.text.lastIndexOf(b.select) : -1
  useDraft.getState().fill(b.text, i >= 0 && b.select ? [i, i + b.select.length] : null)
}

export interface Suggestion {
  lang: 'EN' | 'FR' | 'FI' | 'DE'
  text: string
  short: string
}

export const SUGGESTIONS: Suggestion[] = [
  { lang: 'EN', text: 'How many windows are drawn on this floor plan?', short: 'How many windows?' },
  { lang: 'FR', text: 'Quelle est la surface nette de l’appartement A en m² ?', short: 'Surface nette appartement A' },
  { lang: 'FI', text: 'Montako ovea tässä pohjapiirroksessa on?', short: 'Montako ovea?' },
  { lang: 'DE', text: 'Wie viele Fenster?', short: 'Wie viele Fenster?' },
  { lang: 'EN', text: 'What is the fire rating of the entrance door?', short: 'Fire rating of the door?' },
]

const IFC_SUGGESTIONS: Suggestion[] = [
  { lang: 'EN', text: 'How many windows are on this storey?', short: 'How many windows?' },
  { lang: 'FR', text: 'Quelle est la surface nette totale de cet étage en m² ?', short: 'Surface nette de l’étage' },
  { lang: 'FI', text: 'Montako ovea tässä kerroksessa on?', short: 'Montako ovea?' },
  { lang: 'DE', text: 'Wie viele Räume gibt es auf diesem Geschoss?', short: 'Wie viele Räume?' },
  { lang: 'EN', text: 'List every room with its area on this storey', short: 'List rooms & areas' },
]

const RASTER_SUGGESTIONS: Suggestion[] = [
  { lang: 'EN', text: 'How many doors are shown on this floor plan?', short: 'How many doors?' },
  { lang: 'FR', text: 'Combien de fenêtres figurent sur ce plan ?', short: 'Combien de fenêtres ?' },
  { lang: 'FI', text: 'Montako huonetta tässä pohjapiirroksessa näkyy?', short: 'Montako huonetta?' },
  { lang: 'DE', text: 'Welche Räume und Flächen sind im Plan beschriftet?', short: 'Räume & Flächen?' },
  { lang: 'EN', text: 'Summarize the rooms and printed dimensions on this sheet', short: 'Summarize rooms & dimensions' },
]

/** Capability-aware starter questions for the active drawing (preserves exact SUGGESTIONS on bundled/Swiss CAD). */
export function suggestionsFor(drawing?: Drawing): Suggestion[] {
  if (!drawing || drawing.id === 'swiss-46639') return SUGGESTIONS
  if (drawing.kind === 'raster') return RASTER_SUGGESTIONS
  const caps = new Set<Subject>(drawing.capabilities ?? [])
  if (drawing.kind === 'ifc' || (!caps.has('apartment') && !caps.has('washbasin') && drawing.rooms.length > 0)) {
    return IFC_SUGGESTIONS
  }
  const unitId = drawing.units[0]?.id
  const areaSuggestion: Suggestion =
    unitId && caps.has('apartment')
      ? {
          lang: 'FR',
          text: `Quelle est la surface nette de l’appartement ${unitId} en m² ?`,
          short: `Surface nette appartement ${unitId}`,
        }
      : {
          lang: 'FR',
          text: 'Quelle est la surface nette totale de cet étage en m² ?',
          short: 'Surface nette de l’étage',
        }
  const tailSuggestion: Suggestion =
    drawing.id.startsWith('swiss-')
      ? SUGGESTIONS[4]
      : { lang: 'EN', text: 'List every room with its area on this floor plan', short: 'List rooms & areas' }
  return [SUGGESTIONS[0], areaSuggestion, SUGGESTIONS[2], SUGGESTIONS[3], tailSuggestion]
}

/** Capability-aware prompt templates using valid `PB_LABEL` keys (`Count`, `Area`, `Where is`, `Check`, `List`). */
export function promptBlocksFor(drawing?: Drawing): PromptBlock[] {
  if (!drawing || drawing.id === 'swiss-46639') return PROMPT_BLOCKS
  if (drawing.kind === 'raster') {
    return [
      { label: 'Count', text: 'How many windows are shown on this floor plan?', select: 'windows' },
      { label: 'Area', text: 'What room areas are printed on this drawing?', select: 'room areas' },
      { label: 'Where is', text: 'Where is the main entrance on this plan?', select: 'main entrance' },
      { label: 'Check', text: 'Check the door count on this floor plan', select: 'door' },
      { label: 'List', text: 'List the rooms visible on this floor plan', select: 'rooms' },
    ]
  }
  const caps = new Set<Subject>(drawing.capabilities ?? [])
  if (drawing.kind === 'ifc' || (!caps.has('apartment') && !caps.has('washbasin') && drawing.rooms.length > 0)) {
    const sampleRoom = drawing.rooms.find((r) => r.name.trim().length > 0)?.name.trim()
    return [
      { label: 'Count', text: 'How many doors are on this storey?', select: 'doors' },
      { label: 'Area', text: 'What is the total floor area of this storey?', select: 'this storey' },
      {
        label: 'Where is',
        text: sampleRoom ? `Where is ${sampleRoom}?` : 'Where are the doors on this storey?',
        select: sampleRoom ?? 'doors',
      },
      { label: 'Check', text: 'Check the window count on this storey', select: 'window' },
      { label: 'List', text: 'List every room on this storey', select: 'room' },
    ]
  }
  const unitId = drawing.units[0]?.id
  const areaBlock: PromptBlock =
    unitId && caps.has('apartment')
      ? { label: 'Area', text: `What is the net floor area of apartment ${unitId}?`, select: unitId }
      : { label: 'Area', text: 'What is the total net floor area of this floor plan?', select: 'this floor plan' }
  const whereTarget = caps.has('washbasin') ? 'washbasins' : 'doors'
  return [
    PROMPT_BLOCKS[0],
    areaBlock,
    { label: 'Where is', text: `Where are the ${whereTarget}?`, select: whereTarget },
    PROMPT_BLOCKS[3],
    PROMPT_BLOCKS[4],
  ]
}

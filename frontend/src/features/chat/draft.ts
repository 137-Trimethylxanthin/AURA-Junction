import { create } from 'zustand'
import type { Drawing, ProjectBundle, Subject } from '@/api/contract'

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
  { lang: 'EN', text: 'How many apartments are there?', short: 'How many apartments?' },
  { lang: 'EN', text: 'Show all stairs and where they lead to', short: 'Stairs & where they lead' },
]

export const STAIRS_SUGGESTION: Suggestion = {
  lang: 'EN',
  text: 'Show all stairs and where they lead to',
  short: 'Stairs & where they lead',
}

export const MATCH_FLOORS_SUGGESTION: Suggestion = {
  lang: 'EN',
  text: 'Match stairs and shafts across all floors',
  short: 'Match stairs across floors',
}

const IFC_SUGGESTIONS: Suggestion[] = [
  { lang: 'EN', text: 'How many windows are on this storey?', short: 'How many windows?' },
  { lang: 'FR', text: 'Quelle est la surface nette totale de cet étage en m² ?', short: 'Surface nette de l’étage' },
  { lang: 'FI', text: 'Montako ovea tässä kerroksessa on?', short: 'Montako ovea?' },
  { lang: 'DE', text: 'Wie viele Räume gibt es auf diesem Geschoss?', short: 'Wie viele Räume?' },
  STAIRS_SUGGESTION,
]

const LARGEST_ROOM_DE: Suggestion = { lang: 'DE', text: 'Welcher Raum ist der größte?', short: 'Größter Raum?' }

const RASTER_SUGGESTIONS: Suggestion[] = [
  { lang: 'EN', text: 'How many doors are shown on this floor plan?', short: 'How many doors?' },
  { lang: 'FR', text: 'Combien de fenêtres figurent sur ce plan ?', short: 'Combien de fenêtres ?' },
  { lang: 'FI', text: 'Montako huonetta tässä pohjapiirroksessa näkyy?', short: 'Montako huonetta?' },
  LARGEST_ROOM_DE,
  STAIRS_SUGGESTION,
]

/** Offered when the usual questions do not fit: each still passes the capability filter. */
const FALLBACK_SUGGESTIONS: Suggestion[] = [
  { lang: 'EN', text: 'How many doors are shown on this floor plan?', short: 'How many doors?' },
  { lang: 'EN', text: 'How many windows are drawn on this floor plan?', short: 'How many windows?' },
  LARGEST_ROOM_DE,
]

const OVERVIEW_SUGGESTION: Suggestion = { lang: 'EN', text: 'What does this drawing show?', short: 'What does it show?' }

const STAIR_WORDS = /stair|trepp|stiege|escalier|\btrapp|scala|porras/i

/** A room the store typed as a staircase, or one whose printed name says so. */
function hasStairs(drawing: Drawing): boolean {
  return drawing.rooms.some(
    (r) => STAIR_WORDS.test((r as { room_type?: string }).room_type ?? '') || STAIR_WORDS.test(r.name ?? ''),
  )
}

/**
 * Does the drawing hold what a starter question asks about? Windows and doors follow the drawing's
 * capabilities, room and area questions need rooms, stair questions a stair room, apartment questions units.
 * A drawing without a capability list (older bundles) keeps every question.
 */
export function suggestionFits(s: Suggestion, drawing?: Drawing): boolean {
  if (!drawing || !drawing.capabilities || drawing.id === 'swiss-46639') return true
  const caps = new Set<Subject>(drawing.capabilities)
  const t = s.text
  if (/window|fenêtre|fenster|ikkun/i.test(t) && !caps.has('window')) return false
  if (/\bdoors?\b|\bove[an]\b|\bportes?\b|\btüre?n?\b/i.test(t) && !/fire rating/i.test(t) && !caps.has('door')) return false
  if (/stairs?|shafts?/i.test(t)) return hasStairs(drawing)
  if (/apartments?|appartement/i.test(t)) return drawing.units.length > 0
  if (/rooms?\b|räume|raum\b|huone|surface|\barea\b|fläche/i.test(t)) return drawing.rooms.length > 0
  return true
}

/** What a starter question is about, so a fallback never repeats a question in another language. */
function topicOf(s: Suggestion): string {
  if (/window|fenêtre|fenster|ikkun/i.test(s.text)) return 'window'
  if (/\bdoors?\b|\bove[an]\b|\bportes?\b|\btüre?n?\b/i.test(s.text)) return 'door'
  return s.text
}

function fitted(list: Suggestion[], drawing?: Drawing): Suggestion[] {
  if (!drawing || drawing.id === 'swiss-46639' || !drawing.capabilities) return list
  const kept = list.filter((s) => suggestionFits(s, drawing))
  for (const f of FALLBACK_SUGGESTIONS) {
    if (kept.length >= 3) break
    if (suggestionFits(f, drawing) && !kept.some((k) => topicOf(k) === topicOf(f))) kept.push(f)
  }
  return kept.length >= 3 ? kept : [...kept, OVERVIEW_SUGGESTION]
}

function inActiveProject(drawing: Drawing | undefined, activeProject?: ProjectBundle | null): boolean {
  if (!activeProject || activeProject.floors.length < 2) return false
  return !drawing || activeProject.floors.some((f) => f.drawing_id === drawing.id)
}

/** Questions about the whole building, for the "All N floors" scope: none of them says "this floor". */
const ALL_FLOORS_SUGGESTIONS: Suggestion[] = [
  MATCH_FLOORS_SUGGESTION,
  { lang: 'EN', text: 'How many apartments are there across all floors?', short: 'Apartments across floors' },
  { lang: 'EN', text: 'How many doors are there across all floors?', short: 'Doors across floors' },
  { lang: 'EN', text: 'How many windows are there across all floors?', short: 'Windows across floors' },
  { lang: 'EN', text: 'What is the total floor area across all floors?', short: 'Floor area across floors' },
  { lang: 'EN', text: 'How many rooms are there across all floors?', short: 'Rooms across floors' },
]

/**
 * Starter questions for the active drawing: only questions the drawing can answer (see `suggestionFits`).
 * The single bundled Swiss sample keeps the exact SUGGESTIONS; `allFloors` = the "All N floors" scope is on.
 */
export function suggestionsFor(
  drawing?: Drawing,
  activeProject?: ProjectBundle | null,
  allFloors = false,
): Suggestion[] {
  const base = singleDrawingSuggestions(drawing)
  if (!inActiveProject(drawing, activeProject)) return fitted(base, drawing)
  if (allFloors && drawing) {
    const list = ALL_FLOORS_SUGGESTIONS.filter((s) => suggestionFits(s, drawing))
    return list.length ? list : [OVERVIEW_SUGGESTION]
  }

  const caps = new Set<Subject>(drawing?.capabilities ?? [])
  const hasApartments = Boolean(drawing && (caps.has('apartment') || drawing.units.length > 0))
  const crossCount: Suggestion = hasApartments
    ? { lang: 'EN', text: 'How many apartments are there across all floors?', short: 'Apartments across floors' }
    : { lang: 'EN', text: 'How many doors are there across all floors?', short: 'Doors across floors' }

  const dedupedRest = base.filter(
    (s) =>
      s.text !== MATCH_FLOORS_SUGGESTION.text &&
      s.text !== STAIRS_SUGGESTION.text &&
      s.text !== crossCount.text &&
      suggestionFits(s, drawing),
  )
  const lead = [MATCH_FLOORS_SUGGESTION, STAIRS_SUGGESTION, crossCount].filter((s) => suggestionFits(s, drawing))
  const list = [...lead, ...dedupedRest.slice(0, 6 - lead.length)]
  return list.length ? list : [OVERVIEW_SUGGESTION]
}

function singleDrawingSuggestions(drawing?: Drawing): Suggestion[] {
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
  const apartmentsSuggestion: Suggestion =
    caps.has('apartment') && drawing.units.length > 0
      ? SUGGESTIONS[5]
      : { lang: 'EN', text: 'List every room with its area on this floor plan', short: 'List rooms & areas' }
  return [SUGGESTIONS[0], areaSuggestion, apartmentsSuggestion, STAIRS_SUGGESTION, SUGGESTIONS[3]]
}

/** Capability-aware prompt templates using valid `PB_LABEL` keys (`Count`, `Area`, `Where is`, `Check`, `List`). */
export function promptBlocksFor(drawing?: Drawing, activeProject?: ProjectBundle | null): PromptBlock[] {
  if (inActiveProject(drawing, activeProject)) {
    const caps = new Set<Subject>(drawing?.capabilities ?? [])
    const countSubject = caps.has('apartment') || (drawing?.units.length ?? 0) > 0 ? 'apartments' : 'doors'
    return [
      { label: 'Count', text: `How many ${countSubject} are there across all floors?`, select: countSubject },
      { label: 'Area', text: 'What is the total floor area across all floors?', select: 'all floors' },
      { label: 'Where is', text: 'Show all stairs and where they lead to', select: 'stairs' },
      { label: 'Check', text: 'Match stairs and shafts across all floors', select: 'stairs and shafts' },
      { label: 'List', text: 'List every floor and its rooms in this project', select: 'rooms' },
    ]
  }
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

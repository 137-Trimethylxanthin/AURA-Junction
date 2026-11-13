import { create } from 'zustand'

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

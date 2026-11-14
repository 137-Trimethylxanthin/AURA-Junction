import type { AbstainReason, Lang, Override, Scope, Subject } from './contract'

/**
 * Golden questions with the answers any source (local or API) must give for swiss-46639.
 * Values come from the dataset (questions.jsonl) and from the drawing's CAD geometry.
 */
export interface ConformanceCase {
  name: string
  question: string
  scope?: Scope
  overrides?: Override[]
  lang?: Lang
  expect:
    | { kind: 'count'; value: number; subject: Subject }
    | { kind: 'area'; value: number }
    | { kind: 'abstain'; reason: AbstainReason }
  /** null: no gold expected; undefined: not checked. */
  gold?: { value: number; match: boolean; dataset_qid?: string } | null
}

export const MOCK_REGION = { x: 47.4, y: 39.2, w: 13.6, h: 15.2 }

export const CONFORMANCE: ConformanceCase[] = [
  // Counts from the evidence layers
  {
    name: 'windows (EN, mockup suggestion)',
    question: 'How many windows are drawn on this floor plan?',
    lang: 'en',
    expect: { kind: 'count', value: 12, subject: 'window' },
    gold: { value: 12, match: true, dataset_qid: 'questions.jsonl:103425' },
  },
  {
    name: 'doors (EN)',
    question: 'How many doors are drawn on this floor plan?',
    lang: 'en',
    expect: { kind: 'count', value: 22, subject: 'door' },
    gold: { value: 22, match: true },
  },
  {
    name: 'washbasins (EN)',
    question: 'How many washbasins are drawn on this floor plan?',
    lang: 'en',
    expect: { kind: 'count', value: 5, subject: 'washbasin' },
    gold: { value: 5, match: true },
  },
  {
    name: 'windows in apartment A',
    question: 'How many windows are drawn in apartment A?',
    lang: 'en',
    expect: { kind: 'count', value: 7, subject: 'window' },
    gold: null,
  },
  {
    name: 'washbasins in a marked region',
    question: 'Combien de lavabos ici ?',
    scope: { kind: 'region', rect: MOCK_REGION, label: 'Area 1' },
    lang: 'fr',
    expect: { kind: 'count', value: 3, subject: 'washbasin' },
    gold: null,
  },
  // Counts from rooms
  {
    name: 'rooms labelled Chambre à coucher (EN quoting a French label)',
    question: 'How many rooms are labelled Chambre à coucher?',
    lang: 'en',
    expect: { kind: 'count', value: 6, subject: 'room' },
    gold: { value: 6, match: true, dataset_qid: 'questions.jsonl:103411' },
  },
  {
    name: 'bathrooms',
    question: 'How many bathrooms are there?',
    lang: 'en',
    expect: { kind: 'count', value: 5, subject: 'room' },
    gold: { value: 5, match: true },
  },
  {
    name: 'apartments',
    question: 'How many apartments are on this floor plan?',
    lang: 'en',
    expect: { kind: 'count', value: 2, subject: 'apartment' },
    gold: { value: 2, match: true },
  },
  // Areas
  {
    name: 'area of apartment A',
    question: 'What is the net floor area of apartment A?',
    lang: 'en',
    expect: { kind: 'area', value: 139.34 },
    gold: { value: 139.34, match: true, dataset_qid: 'questions.jsonl:103441' },
  },
  {
    name: 'area of apartment B (within 2 %)',
    question: 'What is the net floor area of apartment B?',
    lang: 'en',
    expect: { kind: 'area', value: 90.55 },
    gold: { value: 90.54, match: true },
  },
  {
    name: 'area of rooms labelled Chambre à coucher',
    question: 'What is the total area of rooms labelled Chambre à coucher?',
    lang: 'en',
    expect: { kind: 'area', value: 88.09 },
    gold: { value: 88.09, match: true },
  },
  {
    name: 'area of all indoor rooms (within 2 % of the dataset)',
    question: 'What is the total net floor area of all numbered indoor rooms?',
    lang: 'en',
    expect: { kind: 'area', value: 261.96 },
    gold: { value: 258.0, match: true },
  },
  {
    name: 'area of room 3.04',
    question: 'What is the net floor area of room 3.04?',
    lang: 'en',
    expect: { kind: 'area', value: 17.04 },
    gold: { value: 17.04, match: true },
  },
  {
    name: 'area of room 3.17',
    question: 'What is the area of room 3.17 in m²?',
    lang: 'en',
    expect: { kind: 'area', value: 31.11 },
    gold: { value: 31.11, match: true },
  },
  {
    name: 'what-if: room 3.04 at 4.135 × 3.60 m',
    question: 'What is the net floor area of apartment A?',
    overrides: [{ room_id: '3.04', w_m: 4.135, h_m: 3.6 }],
    lang: 'en',
    expect: { kind: 'area', value: 137.19 },
    gold: null,
  },
  // Abstains
  // Fixture counts (wc-*, tub-*, shower-* evidence)
  {
    name: 'WCs',
    question: 'How many WCs are there?',
    lang: 'en',
    expect: { kind: 'count', value: 5, subject: 'wc' },
    gold: { value: 5, match: true },
  },
  {
    name: 'bathtubs',
    question: 'How many bathtubs are drawn on this floor plan?',
    lang: 'en',
    expect: { kind: 'count', value: 2, subject: 'bathtub' },
    gold: { value: 2, match: true },
  },
  {
    name: 'showers',
    question: 'How many showers are drawn on this floor plan?',
    lang: 'en',
    expect: { kind: 'count', value: 2, subject: 'shower' },
    gold: { value: 2, match: true },
  },
  {
    name: 'fire rating → not on drawing (mockup suggestion)',
    question: 'What is the fire rating of the entrance door?',
    lang: 'en',
    expect: { kind: 'abstain', reason: 'not_on_drawing' },
  },
  {
    name: 'unknown room → unclear',
    question: 'What is the area of room 9.99?',
    expect: { kind: 'abstain', reason: 'unclear_question' },
  },
  {
    name: 'gibberish → unclear',
    question: 'Tell me a joke',
    expect: { kind: 'abstain', reason: 'unclear_question' },
  },
  // FR / FI / DE phrasings
  {
    name: 'FR windows',
    question: 'Combien de fenêtres y a-t-il sur ce plan ?',
    lang: 'fr',
    expect: { kind: 'count', value: 12, subject: 'window' },
    gold: { value: 12, match: true, dataset_qid: 'questions.jsonl:103426' },
  },
  {
    name: 'FR doors',
    question: 'Combien de portes sont dessinées sur ce plan ?',
    lang: 'fr',
    expect: { kind: 'count', value: 22, subject: 'door' },
  },
  {
    name: 'FR area of apartment A (mockup suggestion)',
    question: 'Quelle est la surface nette de l’appartement A en m² ?',
    lang: 'fr',
    expect: { kind: 'area', value: 139.34 },
    gold: { value: 139.34, match: true, dataset_qid: 'questions.jsonl:103442' },
  },
  {
    name: 'FI windows',
    question: 'Montako ikkunaa tässä pohjapiirroksessa on?',
    lang: 'fi',
    expect: { kind: 'count', value: 12, subject: 'window' },
  },
  {
    name: 'FI doors (mockup suggestion)',
    question: 'Montako ovea tässä pohjapiirroksessa on?',
    lang: 'fi',
    expect: { kind: 'count', value: 22, subject: 'door' },
  },
  {
    name: 'FI area of apartment A',
    question: 'Mikä on A-asunnon pinta-ala?',
    lang: 'fi',
    expect: { kind: 'area', value: 139.34 },
  },
  {
    name: 'DE windows',
    question: 'Wie viele Fenster?',
    lang: 'de',
    expect: { kind: 'count', value: 12, subject: 'window' },
  },
  {
    name: 'DE doors',
    question: 'Wie viele Türen gibt es auf diesem Plan?',
    lang: 'de',
    expect: { kind: 'count', value: 22, subject: 'door' },
  },
  {
    name: 'DE area of apartment A',
    question: 'Wie groß ist die Wohnfläche der Wohnung A?',
    lang: 'de',
    expect: { kind: 'area', value: 139.34 },
  },
]

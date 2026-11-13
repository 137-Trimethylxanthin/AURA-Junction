import type { AbstainReason, RoomId, Subject } from '@/api/contract'

/** Text-derived scope (the region scope comes from a mark, never from text). */
export type TextScope =
  | { kind: 'unit'; unit: string }
  | { kind: 'room'; room_id: RoomId }
  | { kind: 'label'; label: string }

export type NotOnDrawingTopic = 'fire' | 'height' | 'material'

export type Route =
  | { intent: 'count'; subject: Subject; scope: TextScope | null }
  | { intent: 'area'; scope: TextScope | null }
  | { intent: 'abstain'; reason: AbstainReason; topic?: NotOnDrawingTopic }

// Start-anchored (word may continue: stems) and fully anchored word patterns, Unicode-aware.
const S = (src: string) => new RegExp(`(?<![\\p{L}\\d])(?:${src})`, 'iu')
const W = (src: string) => new RegExp(`(?<![\\p{L}\\d])(?:${src})(?![\\p{L}\\d])`, 'iu')

const NOT_ON_DRAWING: [NotOnDrawingTopic, RegExp][] = [
  ['fire', S('fire|incendie|coupe-feu|feu|palo|brandschutz|feuer|brand')],
  ['height', S('height|ceiling height|hauteur|korkeus|huonekorkeus|höhe|raumhöhe')],
  ['material', S('material|matériau|materiaali|rakenne|baustoff')],
]

// Order matters: specific fixtures before doors, fixtures before room labels ("Badewanne" vs "Bad").
const SUBJECTS: [Subject, RegExp][] = [
  ['window', S('windows?|fenêtres?|fenetres?|ikkun|fenster')],
  ['washbasin', S('wash ?basins?|hand ?basins?|basins?|sinks?|lavabos?|pesual|käsienpesu|waschbecken|waschtisch')],
  ['bathtub', S('bath ?tubs?|tubs?|baignoires?|kylpyam|badewanne')],
  ['shower', S('showers?|douches?|suihku|dusche')],
  ['wc', W('wcs?|wc-istuim\\p{L}*|wc-istuin|toilets?|toilettes?|vessa\\p{L}*|klosett')],
  ['door', W('doors?|portes?|ovi|ovea|ovia|ovet|ovien|ovesta|ovessa|türen|tür|türe')],
]

const AREA = /area|surface|superficie|pinta-?ala|neliö|fläche|quadratmeter|m²|m2\b|square met|how big|how large|wie groß|wie gross|kuinka suuri/iu
const COUNT = /how many|number of|count|combien|nombre d|montako|kuinka monta|wie viele|wieviele|anzahl|lukumäärä/iu
const APARTMENT = S('apartments?|flats?|dwellings?|appartements?|logements?|asunto|asunno|asuntoj|huoneisto|wohnung')
const ROOM_WORD = S('rooms?|pièces?|huone|huonei|tilo|zimmer|räume|raum')

/** Drawing labels (as printed, French) and their synonyms in en/fr/fi/de. */
export const LABEL_SYNONYMS: [label: string, re: RegExp][] = [
  ['Chambre à coucher', S('chambres? à coucher|chambres?|bed ?rooms?|makuuhuone|schlafzimmer')],
  ['Séjour/repas', S('séjour|sejour|living|dining|lounge|olohuone|oleskelu|ruokailu|wohnzimmer|wohn-|esszimmer|wohnraum')],
  ['Cuisine', S('cuisines?|kitchens?|keitti|küche|kuche')],
  ['Bain', W('bains?|salles? de bains?|bathrooms?|baths?|kylpyhuone\\p{L}*|bad|badezimmer|bäder')],
  ['Réduit', S('réduits?|reduit|storage|store ?rooms?|closets?|varasto|abstellraum|reduit|abstellr')],
  ['Couloir', S('couloirs?|corridors?|hallways?|halls?|käytäv|eteinen|eteis|flur|korridor|gang')],
  ['Terrasse', S('terrasses?|terraces?|balcon|balcony|terassi|parveke|terrasse|balkon')],
]

const UNIT_RE =
  /(?<![\p{L}])(?:apartment|appartement|logement|unit|wohnung|asunto|asunno\p{L}*|app\.?)\s*[-:]?\s*([A-Ba-b])(?![\p{L}\d])/iu
const UNIT_FI_RE = /(?<![\p{L}])([AB])-(?:asunno|asunto)\p{L}*/u

/** Apartment letter named in the question ("apartment A", "l’appartement B", "asunnossa A", "A-asunnossa"). */
export function unitOf(text: string): string | null {
  const t = text || ''
  const m = t.match(UNIT_RE)
  if (m) {
    const letter = m[1]
    const rest = t.slice((m.index ?? 0) + m[0].length)
    // A lowercase "a" is accepted only at the end of the clause (otherwise it is likely an article).
    if (letter === letter.toUpperCase() || /^\s*([?.!,;]|$)/.test(rest)) return letter.toUpperCase()
  }
  const f = t.match(UNIT_FI_RE)
  return f ? f[1] : null
}

/** Room number like "3.04". */
export function roomNumberOf(text: string): RoomId | null {
  const m = (text || '').match(/(?<![\d.])\d\.\d\d(?![\d])/)
  return m ? m[0] : null
}

/** Drawing label named in the question, by synonym. */
export function labelOf(text: string): string | null {
  for (const [label, re] of LABEL_SYNONYMS) if (re.test(text || '')) return label
  return null
}

function scopeOf(text: string): TextScope | null {
  const room = roomNumberOf(text)
  if (room) return { kind: 'room', room_id: room }
  const unit = unitOf(text)
  if (unit) return { kind: 'unit', unit }
  const label = labelOf(text)
  if (label) return { kind: 'label', label }
  return null
}

export function route(text: string): Route {
  const s = (text || '').normalize('NFC')
  for (const [topic, re] of NOT_ON_DRAWING) if (re.test(s)) return { intent: 'abstain', reason: 'not_on_drawing', topic }

  const scope = scopeOf(s)
  const isArea = AREA.test(s)
  const isCount = COUNT.test(s)
  let subject: Subject | null = null
  for (const [subj, re] of SUBJECTS) {
    if (re.test(s)) {
      subject = subj
      break
    }
  }

  if (subject && !(isArea && !isCount)) return { intent: 'count', subject, scope }
  if (isArea) return { intent: 'area', scope }

  if (isCount && APARTMENT.test(s) && !(scope && scope.kind === 'unit'))
    return { intent: 'count', subject: 'apartment', scope: null }
  if (scope && scope.kind === 'label' && (isCount || ROOM_WORD.test(s)))
    return { intent: 'count', subject: 'room', scope }
  if (isCount && ROOM_WORD.test(s)) return { intent: 'count', subject: 'room', scope }

  return { intent: 'abstain', reason: 'unclear_question' }
}

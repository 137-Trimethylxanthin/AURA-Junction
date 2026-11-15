import type { Lang } from '@/api/contract'

// Room labels on the drawing are French; strip them so an English question that quotes one stays English.
const LABEL_NAMES = /chambre à coucher|séjour\s*\/\s*repas|réduit|terrasse|couloir|cuisine/giu

// `(?<![\p{L}])` anchors: JS `\b` is ASCII-only and misfires next to ä/ö/ü/é.
const L = (src: string) => new RegExp(`(?<![\\p{L}])(?:${src})(?![\\p{L}])`, 'giu')
const S = (src: string) => new RegExp(`(?<![\\p{L}])(?:${src})`, 'giu')

const MARKERS: Record<Lang, RegExp[]> = {
  de: [
    L('wie|viele|wieviele|welche|gibt|es|der|die|das|den|dem|ist|sind|hat|auf|im|in der|diesem|dieser|plan|und|groß|gross'),
    S('fenster|tür|wohnung|fläche|wohnfläche|zimmer|räume|raum|waschbeck|küche|grundriss|badezimmer|badewanne|dusche|brandschutz|anzahl|quadratmeter|stockwerk|geschoss'),
  ],
  fi: [
    L('montako|kuinka|monta|paljonko|onko|missä|mikä|mitkä|tässä|on|ja|siinä|tämän|näitä'),
    S('ikkun|ovea|ovia|ovet|asunno|asunto|huonee|huone|pinta-ala|pohja|keittiö|kylpy|makuu|pesual|vessa|suihku|kerro|neliö'),
  ],
  fr: [
    L('combien|quelle|quel|quels|quelles|est|sont|le|la|les|de|des|du|en|dans|sur|ce|cette|ici|il|y a|a-t-il'),
    S('fenêtre|fenetre|porte|lavabo|appartement|pièce|surface|superficie|baignoire|douche|toilette|étage|plan'),
  ],
  it: [
    L('quanti|quante|quanto|quanta|qual|quale|quali|dove|ci sono|è|sono|il|lo|la|gli|le|di|del|della|dei|delle|nel|nella|su|questo|questa|che|più'),
    S('finestr|porta|porte|lavab|appartament|locale|locali|stanz|superficie|vasca|doccia|bagno|cucina|camer|piano|pianta|larg|lung|scala'),
  ],
  en: [
    L('how|many|much|what|which|is|are|the|of|in|on|this|there|drawn|labelled|labeled|rooms?|total|area|floor|plan|apartment'),
    S('window|door|washbasin|bathroom|bedroom|kitchen|toilet|shower|bathtub'),
  ],
}

// Single characters weigh half a word.
const CHARS: Partial<Record<Lang, RegExp>> = {
  de: /[üß]/gu,
  fi: /[äö]/gu,
  fr: /[éèêçàùâîôœ]/gu,
  it: /[ìò]/gu,
}

const TIE_ORDER: Lang[] = ['de', 'fi', 'fr', 'it', 'en']

export function langScores(text: string): Record<Lang, number> {
  const s = (text || '').toLowerCase().replace(LABEL_NAMES, ' ')
  const out = { en: 0, fr: 0, fi: 0, de: 0, it: 0 } as Record<Lang, number>
  for (const lang of TIE_ORDER) {
    for (const re of MARKERS[lang]) out[lang] += (s.match(re) || []).length
    const ch = CHARS[lang]
    if (ch) out[lang] += 0.5 * (s.match(ch) || []).length
  }
  return out
}

/** Best-guess language of a short question; 'en' when there is no signal. */
export function detect(text: string): Lang {
  const scores = langScores(text)
  let best: Lang = 'en'
  let bestScore = 0
  for (const lang of TIE_ORDER) {
    if (scores[lang] > bestScore) {
      best = lang
      bestScore = scores[lang]
    }
  }
  return best
}

/** True when detect() had any signal to go on. */
export function hasLangSignal(text: string): boolean {
  const scores = langScores(text)
  return TIE_ORDER.some((l) => scores[l] > 0)
}

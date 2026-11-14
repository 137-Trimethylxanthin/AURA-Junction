import type { BoxSubjectAny, Display, Lang } from '@/api/contract'

export const LOCALE: Record<Lang, string> = { en: 'en-GB', fr: 'fr-FR', fi: 'fi-FI', de: 'de-DE' }

const areaFmt = new Map<Lang, Intl.NumberFormat>()
const countFmt = new Map<Lang, Intl.NumberFormat>()
const plural = new Map<Lang, Intl.PluralRules>()

function cached<T>(m: Map<Lang, T>, lang: Lang, make: () => T): T {
  let v = m.get(lang)
  if (!v) {
    v = make()
    m.set(lang, v)
  }
  return v
}

/** Area with exactly 2 decimals in the language's convention (139.34 / 139,34). */
export function fmtArea(n: number, lang: Lang): string {
  return cached(areaFmt, lang, () =>
    new Intl.NumberFormat(LOCALE[lang], { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  ).format(n)
}

/** 12' 6" (whole inches). */
export function fmtFtIn(feet: number): string {
  const total = Math.round(Math.abs(feet) * 12)
  return `${feet < 0 ? '-' : ''}${Math.floor(total / 12)}' ${total % 12}"`
}

/** About four significant figures, at most 2 decimals (4 below 1): 3,675 / 41.01 / 0.0843. */
export function fmtConverted(n: number, lang: Lang): string {
  const mag = n === 0 ? 0 : Math.floor(Math.log10(Math.abs(n)))
  const digits = Math.max(0, Math.min(3 - mag, Math.abs(n) >= 1 ? 2 : 4))
  return new Intl.NumberFormat(LOCALE[lang], {
    minimumFractionDigits: Math.min(digits, 2),
    maximumFractionDigits: digits,
  }).format(n)
}

/**
 * A metric number of a response in the unit its question asked for: ["3,675", "sq ft"], or null when
 * the response has no display unit for that dimension (the caller then shows the metric value).
 */
export function displayParts(
  metric: number,
  metricUnit: 'm' | 'm2' | 'm3',
  display: Display | undefined,
  lang: Lang,
): [string, string] | null {
  const factor = display?.factors?.[metricUnit]
  const label = display?.labels?.[metricUnit]
  if (!display || typeof factor !== 'number' || !label) return null
  const v = metric * factor
  if (display.style === 'ft_in' && metricUnit === 'm') return [fmtFtIn(v), '']
  return [fmtConverted(v, lang), label]
}

export function fmtCount(n: number, lang: Lang): string {
  return cached(countFmt, lang, () => new Intl.NumberFormat(LOCALE[lang], { maximumFractionDigits: 0 })).format(n)
}

/** Cost in USD for the footer; "$0" when nothing was spent. */
export function fmtCost(usd: number): string {
  if (usd === 0) return '$0'
  if (usd < 0.01) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(2)}`
}

type Forms = [one: string, other: string]

const NOUNS: Record<Lang, Record<BoxSubjectAny, Forms>> = {
  en: {
    window: ['window', 'windows'],
    door: ['door', 'doors'],
    washbasin: ['washbasin', 'washbasins'],
    wc: ['WC', 'WCs'],
    bathtub: ['bathtub', 'bathtubs'],
    shower: ['shower', 'showers'],
    room: ['room', 'rooms'],
    apartment: ['apartment', 'apartments'],
    label: ['printed label', 'printed labels'],
    fire_rating: ['fire rating', 'fire ratings'],
    bedroom: ['bedroom', 'bedrooms'],
    kitchen: ['kitchen', 'kitchens'],
    bathroom: ['bathroom', 'bathrooms'],
    wc_room: ['WC room', 'WC rooms'],
    sauna: ['sauna', 'saunas'],
    sink: ['sink', 'sinks'],
    shower_or_bathtub: ['shower or bathtub', 'showers or bathtubs'],
    storey: ['storey', 'storeys'],
    balcony: ['balcony', 'balconies'],
  },
  fr: {
    window: ['fenêtre', 'fenêtres'],
    door: ['porte', 'portes'],
    washbasin: ['lavabo', 'lavabos'],
    wc: ['WC', 'WC'],
    bathtub: ['baignoire', 'baignoires'],
    shower: ['douche', 'douches'],
    room: ['pièce', 'pièces'],
    apartment: ['appartement', 'appartements'],
    label: ['texte imprimé', 'textes imprimés'],
    fire_rating: ['résistance au feu', 'résistances au feu'],
    bedroom: ['chambre', 'chambres'],
    kitchen: ['cuisine', 'cuisines'],
    bathroom: ['salle de bains', 'salles de bains'],
    wc_room: ['local WC', 'locaux WC'],
    sauna: ['sauna', 'saunas'],
    sink: ['évier', 'éviers'],
    shower_or_bathtub: ['douche ou baignoire', 'douches ou baignoires'],
    storey: ['étage', 'étages'],
    balcony: ['balcon', 'balcons'],
  },
  // Finnish numerals > 1 take the partitive singular: 1 ikkuna, 12 ikkunaa.
  fi: {
    window: ['ikkuna', 'ikkunaa'],
    door: ['ovi', 'ovea'],
    washbasin: ['pesuallas', 'pesuallasta'],
    wc: ['WC', 'WC:tä'],
    bathtub: ['kylpyamme', 'kylpyammetta'],
    shower: ['suihku', 'suihkua'],
    room: ['huone', 'huonetta'],
    apartment: ['asunto', 'asuntoa'],
    label: ['merkintä', 'merkintää'],
    fire_rating: ['paloluokka', 'paloluokkaa'],
    bedroom: ['makuuhuone', 'makuuhuonetta'],
    kitchen: ['keittiö', 'keittiötä'],
    bathroom: ['kylpyhuone', 'kylpyhuonetta'],
    wc_room: ['wc-tila', 'wc-tilaa'],
    sauna: ['sauna', 'saunaa'],
    sink: ['tiskiallas', 'tiskiallasta'],
    shower_or_bathtub: ['suihku tai kylpyamme', 'suihkua tai kylpyammetta'],
    storey: ['kerros', 'kerrosta'],
    balcony: ['parveke', 'parveketta'],
  },
  de: {
    window: ['Fenster', 'Fenster'],
    door: ['Tür', 'Türen'],
    washbasin: ['Waschbecken', 'Waschbecken'],
    wc: ['WC', 'WCs'],
    bathtub: ['Badewanne', 'Badewannen'],
    shower: ['Dusche', 'Duschen'],
    room: ['Raum', 'Räume'],
    apartment: ['Wohnung', 'Wohnungen'],
    label: ['Beschriftung', 'Beschriftungen'],
    fire_rating: ['Feuerwiderstand', 'Feuerwiderstände'],
    bedroom: ['Schlafzimmer', 'Schlafzimmer'],
    kitchen: ['Küche', 'Küchen'],
    bathroom: ['Badezimmer', 'Badezimmer'],
    wc_room: ['WC-Raum', 'WC-Räume'],
    sauna: ['Sauna', 'Saunas'],
    sink: ['Spülbecken', 'Spülbecken'],
    shower_or_bathtub: ['Dusche oder Badewanne', 'Duschen oder Badewannen'],
    storey: ['Geschoss', 'Geschosse'],
    balcony: ['Balkon', 'Balkone'],
  },
}

/** Noun for `n` items of `subject`, chosen with Intl.PluralRules. */
export function noun(subject: BoxSubjectAny, n: number, lang: Lang): string {
  const rule = cached(plural, lang, () => new Intl.PluralRules(LOCALE[lang])).select(n)
  const [one, other] = NOUNS[lang][subject]
  return rule === 'one' ? one : other
}

/** "12 windows", "1 ikkuna", "22 ovea". */
export function countPhrase(subject: BoxSubjectAny, n: number, lang: Lang): string {
  return `${fmtCount(n, lang)} ${noun(subject, n, lang)}`
}

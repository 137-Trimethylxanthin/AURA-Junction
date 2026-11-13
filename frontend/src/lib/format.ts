import type { Lang, Subject } from '@/api/contract'

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

const NOUNS: Record<Lang, Record<Subject, Forms>> = {
  en: {
    window: ['window', 'windows'],
    door: ['door', 'doors'],
    washbasin: ['washbasin', 'washbasins'],
    wc: ['WC', 'WCs'],
    bathtub: ['bathtub', 'bathtubs'],
    shower: ['shower', 'showers'],
    room: ['room', 'rooms'],
    apartment: ['apartment', 'apartments'],
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
  },
}

/** Noun for `n` items of `subject`, chosen with Intl.PluralRules. */
export function noun(subject: Subject, n: number, lang: Lang): string {
  const rule = cached(plural, lang, () => new Intl.PluralRules(LOCALE[lang])).select(n)
  const [one, other] = NOUNS[lang][subject]
  return rule === 'one' ? one : other
}

/** "12 windows", "1 ikkuna", "22 ovea". */
export function countPhrase(subject: Subject, n: number, lang: Lang): string {
  return `${fmtCount(n, lang)} ${noun(subject, n, lang)}`
}

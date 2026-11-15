import type { Lang } from '@/api/contract'
import { ui, type UiKey } from '@/i18n/strings'
import { countPhrase, fmtCount } from '@/lib/format'

/** The name the store gives a room the sheet prints no label for. */
export const NO_LABEL = 'Room without label'

/** A room name for display: the store's "Room without label" in the interface language, any other name as it is. */
export function roomDisplayName(name: string, lang: Lang): string {
  return name === NO_LABEL ? ui(lang, 'room_no_label') : name
}

const COUNTED = /^(≈)?(\d+) (room|door|window|fixture|object)s?$/
const PAGE = /^page (\d+) of (\d+)$/
const STOREY = /^storey (.+)$/

/**
 * The line under a drawing's title ("PNG · 1:200 · 10 rooms · 10 doors · page 7 of 16") in the interface
 * language. The server composes it in English from counts; each part it is known to write is rewritten, every
 * other part (a format, a scale, a source name) is kept as it is.
 */
export function localizeSubtitle(subtitle: string, lang: Lang): string {
  if (lang === 'en') return subtitle
  return subtitle
    .split(' · ')
    .map((part) => {
      const c = COUNTED.exec(part)
      if (c) {
        const n = Number(c[2])
        const what = c[3] as 'room' | 'door' | 'window' | 'fixture' | 'object'
        const said =
          what === 'fixture' || what === 'object'
            ? ui(lang, `sub_${what}_${n === 1 ? '1' : 'n'}` as UiKey, { n: fmtCount(n, lang) })
            : countPhrase(what, n, lang)
        return `${c[1] ?? ''}${said}`
      }
      const p = PAGE.exec(part)
      if (p) return ui(lang, 'sub_page_of', { n: p[1], m: p[2] })
      const s = STOREY.exec(part)
      if (s) return ui(lang, 'sub_storey', { name: s[1] })
      if (part === 'not interpreted') return ui(lang, 'sub_not_interpreted')
      if (part === 'outlines only') return ui(lang, 'sub_outlines_only')
      return part
    })
    .join(' · ')
}

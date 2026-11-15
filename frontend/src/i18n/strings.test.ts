import { describe, expect, it } from 'vitest'
import { STRINGS, UI, ui } from './strings'

const holes = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()

describe('translations', () => {
  for (const [name, dicts] of [
    ['answer strings', STRINGS],
    ['interface strings', UI],
  ] as const) {
    it(`${name}: every language has the same placeholders as English, and no empty text`, () => {
      const en = dicts.en as Record<string, string>
      for (const lang of ['fr', 'fi', 'de', 'it'] as const) {
        const d = dicts[lang] as Record<string, string>
        for (const k of Object.keys(en)) {
          expect(d[k], `${lang}.${k}`).toBeTruthy()
          expect(holes(d[k]), `${lang}.${k}`).toEqual(holes(en[k]))
        }
      }
    })
  }
  it('fills placeholders', () => {
    expect(ui('de', 'chars', { n: 12, max: 2000 })).toBe('12 / 2000 Zeichen')
  })
})

describe('interface language', () => {
  it('plural forms follow the language and count', async () => {
    const { plural } = await import('./uiLang')
    expect(plural('en', 'pl_csv_rows', 1)).toBe('1 row · your rejects included')
    expect(plural('en', 'pl_csv_rows', 5)).toBe('5 rows · your rejects included')
    expect(plural('de', 'up_rejected', 3)).toBe('3 Dateien nicht hochgeladen')
    expect(plural('fr', 'up_rejected', 1)).toBe('Non téléversé')
  })

  it('switching it changes a toolbar label but never the answer language', async () => {
    const { uiLang } = await import('./uiLang')
    const { t } = await import('./strings')
    const before = t('en', 'net_floor_area')
    uiLang.set('de')
    expect(uiLang.get()).toBe('de')
    expect(ui(uiLang.get(), 'tool_select')).toBe('Auswählen')
    expect(ui(uiLang.get(), 'ctx_actions')).toBe('Planaktionen')
    expect(t('en', 'net_floor_area')).toBe(before)
    expect(t('fr', 'net_floor_area')).not.toBe(before)
    uiLang.set('en')
    expect(ui(uiLang.get(), 'tool_select')).toBe('Select')
  })
})

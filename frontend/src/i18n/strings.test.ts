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
      for (const lang of ['fr', 'fi', 'de'] as const) {
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

import { describe, expect, it } from 'vitest'
import { UI } from '@/i18n/strings'
import { localizeSubtitle, roomDisplayName } from './subtitle'

describe('the counts line of a drawing', () => {
  const line = 'Stress test · PNG · 1:200 · page 7 of 16 · 10 rooms · 1 door · 7 windows · 5 fixtures'
  it('is left alone in English', () => {
    expect(localizeSubtitle(line, 'en')).toBe(line)
  })
  it('reads in the interface language, with formats and scales kept', () => {
    expect(localizeSubtitle(line, 'de')).toBe(
      'Stress test · PNG · 1:200 · Seite 7 von 16 · 10 Räume · 1 Tür · 7 Fenster · 5 Einrichtungsgegenstände',
    )
    const fi = localizeSubtitle(line, 'fi')
    expect(fi).toContain('sivu 7/16')
    expect(fi).not.toMatch(/rooms|doors|windows|fixtures|page/)
    expect(localizeSubtitle('IFC IFC2X3 · storey 1. Sal · 3 objects · outlines only', 'fr')).toBe(
      'IFC IFC2X3 · étage 1. Sal · 3 objets · contours seulement',
    )
    expect(localizeSubtitle('PDF · not interpreted', 'de')).toBe('PDF · nicht interpretiert')
  })
  it('names a room without a label in the interface language', () => {
    expect(roomDisplayName('Room without label', 'de')).toBe('Raum ohne Beschriftung')
    expect(roomDisplayName('Kitchen', 'de')).toBe('Kitchen')
    for (const lang of ['fr', 'fi', 'de'] as const) expect(UI[lang].room_no_label).not.toBe(UI.en.room_no_label)
  })
})

import { describe, expect, it } from 'vitest'
import { detect } from './detect'
import { labelOf, roomNumberOf, route, unitOf } from './route'
import { bboxOf, boxCentreInRect, clampRect, nearestRoom, normaliseRect, pointInPolygon, roomAt } from './geometry'
import { fmtArea, fmtCost, fmtCount, noun } from './format'
import { deriveAnswer, emptyToggles, goldChip } from './answer'
import { bundledDrawing } from '@/data/drawing'
import { ask } from '@/api/local'
import { langDetected, STRINGS, t } from '@/i18n/strings'

describe('detect', () => {
  it.each([
    ['How many windows are drawn on this floor plan?', 'en'],
    ['What is the fire rating of the entrance door?', 'en'],
    ['How many rooms are labelled Chambre à coucher?', 'en'],
    ['What is the total area of the Séjour/repas rooms?', 'en'],
    ['Quelle est la surface nette de l’appartement A en m² ?', 'fr'],
    ['Combien de lavabos ici ?', 'fr'],
    ['Combien de fenêtres y a-t-il ?', 'fr'],
    ['Montako ovea tässä pohjapiirroksessa on?', 'fi'],
    ['Kuinka monta ikkunaa asunnossa A on?', 'fi'],
    ['Paljonko A-asunnossa on pinta-alaa?', 'fi'],
    ['Wie viele Fenster?', 'de'],
    ['Wohnfläche', 'de'],
    ['Wie groß ist die Wohnung B?', 'de'],
    ['Wie viele Türen gibt es?', 'de'],
  ] as const)('%s → %s', (text, lang) => {
    expect(detect(text)).toBe(lang)
  })

  it('falls back to en for empty input', () => {
    expect(detect('')).toBe('en')
    expect(detect('12')).toBe('en')
  })
})

describe('route', () => {
  it('counts subjects', () => {
    expect(route('How many windows are drawn on this floor plan?')).toEqual({ intent: 'count', subject: 'window', scope: null })
    expect(route('Montako ovea tässä pohjapiirroksessa on?')).toMatchObject({ intent: 'count', subject: 'door' })
    expect(route('Combien de lavabos ici ?')).toMatchObject({ intent: 'count', subject: 'washbasin' })
    expect(route('Wie viele Waschbecken?')).toMatchObject({ intent: 'count', subject: 'washbasin' })
    expect(route('Wie viele Badewannen gibt es?')).toMatchObject({ intent: 'count', subject: 'bathtub' })
    expect(route('Montako WC:tä?')).toMatchObject({ intent: 'count', subject: 'wc' })
    expect(route('How many showers?')).toMatchObject({ intent: 'count', subject: 'shower' })
  })

  it('"rooms above 10 m²" is an area question, not doors', () => {
    expect(route('rooms above 10 m²').intent).toBe('area')
  })

  it('FI door stems are anchored (no "ove" inside other words)', () => {
    expect(route('Is the stove above the oven?').intent).toBe('abstain')
    expect(route('Montako ovea?')).toMatchObject({ subject: 'door' })
  })

  it('fire beats door', () => {
    expect(route('What is the fire rating of the entrance door?')).toEqual({
      intent: 'abstain',
      reason: 'not_on_drawing',
      topic: 'fire',
    })
    expect(route('Quel est le degré coupe-feu ?')).toMatchObject({ reason: 'not_on_drawing' })
  })

  it('areas with scopes', () => {
    expect(route('What is the net floor area of apartment A?')).toEqual({ intent: 'area', scope: { kind: 'unit', unit: 'A' } })
    expect(route('What is the area of room 3.04?')).toEqual({ intent: 'area', scope: { kind: 'room', room_id: '3.04' } })
    expect(route('Total area of rooms labelled Chambre à coucher')).toEqual({
      intent: 'area',
      scope: { kind: 'label', label: 'Chambre à coucher' },
    })
    expect(route('Wie groß ist die Wohnfläche?')).toEqual({ intent: 'area', scope: null })
    expect(route('Mikä on asunnon B nettopinta-ala?')).toEqual({ intent: 'area', scope: { kind: 'unit', unit: 'B' } })
  })

  it('room-label counts and apartments', () => {
    expect(route('How many rooms are labelled Bain?')).toEqual({
      intent: 'count',
      subject: 'room',
      scope: { kind: 'label', label: 'Bain' },
    })
    expect(route('Combien de chambres à coucher ?')).toMatchObject({ subject: 'room', scope: { label: 'Chambre à coucher' } })
    expect(route('Montako makuuhuonetta on?')).toMatchObject({ subject: 'room', scope: { label: 'Chambre à coucher' } })
    expect(route('Wie viele Schlafzimmer?')).toMatchObject({ subject: 'room', scope: { label: 'Chambre à coucher' } })
    expect(route('How many apartments are on this floor plan?')).toEqual({ intent: 'count', subject: 'apartment', scope: null })
    expect(route('Combien d’appartements ?')).toMatchObject({ subject: 'apartment' })
  })

  it('fixture counts keep the text scope', () => {
    expect(route('How many windows in apartment B?')).toMatchObject({ subject: 'window', scope: { kind: 'unit', unit: 'B' } })
    expect(route('How many washbasins are in the bathrooms?')).toMatchObject({
      subject: 'washbasin',
      scope: { kind: 'label', label: 'Bain' },
    })
  })

  it('unclear questions abstain', () => {
    expect(route('Tell me a joke')).toEqual({ intent: 'abstain', reason: 'unclear_question' })
  })
})

describe('unitOf / roomNumberOf / labelOf', () => {
  it.each([
    ['What is the net floor area of apartment A?', 'A'],
    ['Quelle est la surface nette de l’appartement A en m² ?', 'A'],
    ['Wie viele Fenster hat die Wohnung B?', 'B'],
    ['Kuinka monta ikkunaa asunnossa B on?', 'B'],
    ['montako ikkunaa siinä A-asunnossa on', 'A'],
    ['area of apartment b', 'B'],
    ['How many apartments are on this floor plan?', null],
    ['Is apartment a good investment?', null],
  ] as const)('%s → %s', (text, unit) => {
    expect(unitOf(text)).toBe(unit)
  })

  it('room numbers and labels', () => {
    expect(roomNumberOf('room 3.04?')).toBe('3.04')
    expect(roomNumberOf('13.045')).toBeNull()
    expect(labelOf('the kitchen')).toBe('Cuisine')
    expect(labelOf('Küche')).toBe('Cuisine')
    expect(labelOf('keittiössä')).toBe('Cuisine')
    expect(labelOf('Séjour/repas')).toBe('Séjour/repas')
    expect(labelOf('windows')).toBeNull()
  })
})

describe('geometry', () => {
  const square: [number, number][] = [
    [0, 0],
    [10, 0],
    [10, 10],
    [0, 10],
  ]
  it('pointInPolygon', () => {
    expect(pointInPolygon(5, 5, square)).toBe(true)
    expect(pointInPolygon(15, 5, square)).toBe(false)
  })

  it('roomAt and nearestRoom on the drawing', () => {
    const r = bundledDrawing.rooms.find((x) => x.room_id === '3.04')!
    expect(roomAt(bundledDrawing.rooms, r.label_at[0], r.label_at[1])?.room_id).toBe('3.04')
    expect(roomAt(bundledDrawing.rooms, 1, 99)).toBeNull()
    expect(nearestRoom(bundledDrawing.rooms, 1, 99)).toBeNull()
    // Every room's label point is in that room or close to it.
    for (const room of bundledDrawing.rooms) {
      expect(nearestRoom(bundledDrawing.rooms, room.label_at[0], room.label_at[1])).not.toBeNull()
    }
  })

  it('boxCentreInRect uses the centre', () => {
    expect(boxCentreInRect({ x: 8, y: 8, w: 4, h: 4 }, { x: 0, y: 0, w: 10, h: 10 })).toBe(true)
    expect(boxCentreInRect({ x: 9, y: 9, w: 4, h: 4 }, { x: 0, y: 0, w: 10, h: 10 })).toBe(false)
  })

  it('normalise and clamp rects', () => {
    expect(normaliseRect({ x: 10, y: 10, w: -4, h: -2 })).toEqual({ x: 6, y: 8, w: 4, h: 2 })
    expect(clampRect({ x: 95, y: -5, w: 10, h: 10 })).toEqual({ x: 95, y: 0, w: 5, h: 5 })
    expect(bboxOf(square)).toEqual({ x: 0, y: 0, w: 10, h: 10 })
  })
})

describe('format', () => {
  it('fmtArea uses the language decimal separator', () => {
    expect(fmtArea(139.34, 'en')).toBe('139.34')
    for (const lang of ['fr', 'fi', 'de'] as const) {
      expect(fmtArea(139.34, lang)).toMatch(/^139,34$/)
      expect(fmtArea(1234.5, lang)).toMatch(/^1\D?234,50$/)
    }
    expect(fmtArea(17, 'en')).toBe('17.00')
  })

  it('fmtCount and fmtCost', () => {
    expect(fmtCount(12, 'fi')).toBe('12')
    expect(fmtCost(0)).toBe('$0')
    expect(fmtCost(0.0002)).toBe('$0.0002')
    expect(fmtCost(1.5)).toBe('$1.50')
  })

  it('plural nouns via Intl.PluralRules', () => {
    expect(noun('window', 1, 'fi')).toBe('ikkuna')
    expect(noun('window', 12, 'fi')).toBe('ikkunaa')
    expect(noun('door', 1, 'fi')).toBe('ovi')
    expect(noun('door', 22, 'fi')).toBe('ovea')
    expect(noun('washbasin', 1, 'fi')).toBe('pesuallas')
    expect(noun('washbasin', 5, 'fi')).toBe('pesuallasta')
    expect(noun('window', 1, 'de')).toBe('Fenster')
    expect(noun('window', 12, 'de')).toBe('Fenster')
    expect(noun('door', 1, 'de')).toBe('Tür')
    expect(noun('door', 22, 'de')).toBe('Türen')
    expect(noun('washbasin', 5, 'de')).toBe('Waschbecken')
    expect(noun('window', 1, 'fr')).toBe('fenêtre')
    expect(noun('window', 12, 'fr')).toBe('fenêtres')
    expect(noun('door', 22, 'fr')).toBe('portes')
    expect(noun('washbasin', 5, 'fr')).toBe('lavabos')
    expect(noun('room', 6, 'en')).toBe('rooms')
    expect(noun('apartment', 1, 'en')).toBe('apartment')
    expect(noun('apartment', 2, 'fi')).toBe('asuntoa')
  })
})

describe('answer.ts', () => {
  const windows = ask({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })

  it('count = boxes not rejected; gold chip goes to user_changed', () => {
    const t = emptyToggles()
    expect(deriveAnswer(windows, t).value).toBe(12)
    expect(deriveAnswer(windows, t).gold.state).toBe('match')
    t.rejected['win-03'] = true
    const d = deriveAnswer(windows, t)
    expect(d.value).toBe(11)
    expect(d.rejected).toEqual(['win-03'])
    expect(d.gold).toMatchObject({ state: 'user_changed', dataset: 12 })
  })

  it('gold chip states', () => {
    const b = ask({ drawing_id: 'swiss-46639', question: 'What is the net floor area of apartment B?' })
    expect(goldChip(b, false).state).toBe('within_tolerance')
    const all = ask({ drawing_id: 'swiss-46639', question: 'What is the net floor area of all numbered indoor rooms?' })
    expect(goldChip(all, false).state).toBe('mismatch')
    const region = ask({
      drawing_id: 'swiss-46639',
      question: 'How many windows?',
      scope: { kind: 'region', rect: { x: 0, y: 0, w: 50, h: 50 }, label: 'Area 1' },
    })
    expect(goldChip(region, false).state).toBe('none')
  })

  it('room counts follow room toggles; apartments count distinct units', () => {
    const bedrooms = ask({ drawing_id: 'swiss-46639', question: 'How many rooms are labelled Chambre à coucher?' })
    const t = emptyToggles()
    t.included['room-3.04'] = false
    expect(deriveAnswer(bedrooms, t).value).toBe(5)
    const apts = ask({ drawing_id: 'swiss-46639', question: 'How many apartments are there?' })
    const u = emptyToggles()
    for (const r of bundledDrawing.rooms.filter((x) => x.unit === 'B')) u.included[`room-${r.room_id}`] = false
    expect(deriveAnswer(apts, u, bundledDrawing).value).toBe(1)
  })

  it('abstain has no value', () => {
    const r = ask({ drawing_id: 'swiss-46639', question: 'How many WCs?' })
    expect(deriveAnswer(r, emptyToggles())).toMatchObject({ kind: 'abstain', value: null, gold: { state: 'none' } })
  })
})

describe('i18n', () => {
  it('every language has every key, non-empty', () => {
    const keys = Object.keys(STRINGS.en)
    for (const lang of ['fr', 'fi', 'de'] as const) {
      expect(Object.keys(STRINGS[lang]).sort()).toEqual([...keys].sort())
      for (const k of keys) expect(STRINGS[lang][k as keyof typeof STRINGS.en]).not.toBe('')
    }
  })

  it('t fills placeholders', () => {
    expect(t('fr', 'where_unit', { unit: 'A' })).toBe('dans l’appartement A')
    expect(t('fi', 'chip_you', { n: 11 })).toBe('sinä: 11')
    expect(langDetected('en', 'fr')).toBe('French detected')
    expect(langDetected('fr', 'fr')).toBe('Français détecté')
  })

  it('what-if questions route back to an area question of the same apartment', () => {
    for (const lang of ['en', 'fr', 'fi', 'de'] as const) {
      const q = t(lang, 'whatif_question_unit', { unit: 'A' })
      expect(route(q)).toEqual({ intent: 'area', scope: { kind: 'unit', unit: 'A' } })
      expect(detect(q)).toBe(lang)
    }
  })
})

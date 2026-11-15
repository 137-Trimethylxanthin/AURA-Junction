import { describe, expect, it } from 'vitest'
import { traceBody, traceHint, traceTag, traceTitle } from './titles'

describe('traceTitle', () => {
  it('keeps English and unknown titles as sent', () => {
    expect(traceTitle('Answer', 'en')).toBe('Answer')
    expect(traceTitle('count_objects', 'de')).toBe('count_objects')
  })
  it('shows the fixed step titles in the interface language', () => {
    expect(traceTitle('Answer', 'de')).toBe('Antwort')
    expect(traceTitle('Question', 'it')).toBe('Domanda')
    expect(traceTitle('Cross-check on the image', 'fi')).toBe('Ristiintarkistus kuvasta')
  })
  it('keeps the model name of a route decision', () => {
    expect(traceTitle('Route decision · gemini-decision-1', 'fr')).toBe('Décision de routage · gemini-decision-1')
  })
})


describe('traceBody', () => {
  it('translates the fixed sentences and keeps everything else', () => {
    expect(traceBody('Abstained. Nothing guessed.', 'de')).toBe('Keine Antwort. Nichts geraten.')
    expect(traceBody('No model has checked this answer.', 'it')).toBe('Nessun modello ha verificato questa risposta.')
    expect(traceBody('36 doors on this floor on the DXF door layer', 'de')).toBe('36 doors on this floor on the DXF door layer')
    expect(traceBody('Abstained. Nothing guessed.', 'en')).toBe('Abstained. Nothing guessed.')
  })
  it('translates sentences around numbers', () => {
    expect(traceBody('36 doors. Each one is drawn on the plan for you to check.', 'de')).toBe(
      '36 Türen. Jedes Element ist zur Kontrolle im Plan eingezeichnet.',
    )
    expect(traceBody('1 window. Each one is drawn on the plan for you to check.', 'fr')).toBe(
      '1 fenêtre. Chacune est tracée sur le plan pour vérification.',
    )
    expect(traceBody('Sum of 31 net room areas = 341.42 m², shown room by room.', 'it')).toBe(
      'Somma di 31 superfici nette dei locali = 341.42 m², mostrata locale per locale.',
    )
    expect(traceBody('5 stairs. Each one is drawn on the plan for you to check.', 'de')).toBe(
      '5 stairs. Each one is drawn on the plan for you to check.',
    )
  })
})

describe('traceTag and traceHint', () => {
  it('translates fixed tags and the detected-language tag', () => {
    expect(traceTag('count', 'de')).toBe('Anzahl')
    expect(traceTag('German · detected', 'de')).toBe('Deutsch · erkannt')
    expect(traceTag('DXF · door layer', 'de')).toBe('DXF · door layer')
    expect(traceTag('count', 'en')).toBe('count')
  })
  it('has a hint for every language', () => {
    for (const l of ['en', 'de', 'fr', 'fi', 'it'] as const) expect(traceHint(l).length).toBeGreaterThan(20)
  })

  it('translates the frequent room and store sentences', () => {
    expect(traceBody('19 rooms. Each room is lit on the plan for you to check.', 'de'))
      .toBe('19 Räume. Jeder Raum ist zur Kontrolle im Plan hervorgehoben.')
    expect(traceBody('21 numbered rooms on this floor plan; 2 outdoor left out.', 'de'))
      .toBe('21 nummerierte Räume auf diesem Grundriss; 2 im Freien nicht mitgezählt.')
    expect(traceBody('0 rooms: no room was read on this sheet.', 'fi')).toBe('0 huonetta: tältä lehdeltä ei luettu yhtään.')
    expect(traceBody("129 doors found by the drawing store's symbol detection (a model estimate).", 'de'))
      .toContain('129 Türen')
    expect(traceBody('Read 19 rooms from the indexed drawing in 0 ms.', 'it')).toContain('19 locali')
    expect(traceBody('19 rooms. Each room is lit on the plan for you to check.', 'en')).toContain('Each room is lit')
  })
})

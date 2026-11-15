import { describe, expect, it } from 'vitest'
import { detect } from './detect'
import { estimateTag, fmtRoomArea, localisedPlanSrc, noun } from './format'

describe('Italian', () => {
  it('is detected from a question', () => {
    expect(detect('Quante porte ci sono?')).toBe('it')
    expect(detect('Qual è il locale più grande?')).toBe('it')
    expect(detect('Combien de portes y a-t-il ?')).toBe('fr')
    expect(detect('How many doors are there?')).toBe('en')
    expect(detect('Wie viele Türen gibt es?')).toBe('de')
  })

  it('has nouns and the estimate tag', () => {
    expect(noun('door', 1, 'it')).toBe('porta')
    expect(noun('door', 3, 'it')).toBe('porte')
    expect(estimateTag('it')).toBe('(stima)')
    expect(fmtRoomArea(31.2, 'estimate', 'it')).toBe('≈ 31 m² (stima)')
  })

  it('asks for a plan image with its footnote in the interface language', () => {
    expect(localisedPlanSrc('/drawings/a/plan.svg?v=1', 'it')).toBe('/drawings/a/plan.svg?v=1&lang=it')
    expect(localisedPlanSrc('/drawings/a/plan.svg', 'de')).toBe('/drawings/a/plan.svg?lang=de')
    expect(localisedPlanSrc('/drawings/a/plan.svg?v=1', 'en')).toBe('/drawings/a/plan.svg?v=1')
    expect(localisedPlanSrc('/drawings/a/original.png', 'it')).toBe('/drawings/a/original.png')
  })
})

import { describe, expect, it } from 'vitest'
import { needsTidy, splitLocally } from './heuristic'

describe('needsTidy (no model)', () => {
  it('offers tidy-up for several questions, joiners, long text and voice', () => {
    expect(needsTidy('How many windows? And how big is room 3.04?')).toBe(true)
    expect(needsTidy('How many sinks are there and also how big is bedroom 3.05')).toBe(true)
    expect(needsTidy('Combien de fenêtres et aussi la surface de l’appartement A')).toBe(true)
    expect(needsTidy('x'.repeat(200))).toBe(true)
    expect(needsTidy('How many windows?', { fromVoice: true })).toBe(true)
  })
  it('stays quiet for one plain question', () => {
    expect(needsTidy('How many windows are drawn on this floor plan?')).toBe(false)
    expect(needsTidy('Doors and windows in apartment A?')).toBe(false)
    expect(needsTidy('hi')).toBe(false)
  })
})

describe('splitLocally', () => {
  it('splits on question marks and clear joiners only', () => {
    expect(splitLocally('How many windows? How big is room 3.04?')).toEqual(['How many windows?', 'How big is room 3.04?'])
    expect(splitLocally('How many sinks are there and also how big is bedroom 3.05')).toEqual([
      'How many sinks are there',
      'How big is bedroom 3.05',
    ])
    expect(splitLocally('Wie viele Fenster und auch wie viele Türen')).toEqual(['Wie viele Fenster', 'Wie viele Türen'])
  })
  it('does not split a single question, even with a plain "and"', () => {
    expect(splitLocally('How many doors and windows are in apartment A?')).toEqual([])
  })
})

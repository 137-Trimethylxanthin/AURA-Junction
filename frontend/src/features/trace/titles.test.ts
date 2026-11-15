import { describe, expect, it } from 'vitest'
import { traceTitle } from './titles'

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

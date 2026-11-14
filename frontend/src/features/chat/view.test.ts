import { checkParts, footerParts } from '@/features/chat/view'
import { ask as askLocalForFooter } from '@/api/local'
import { describe, expect, it } from 'vitest'

describe('check cost in the footer', () => {
  const base = askLocalForFooter({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })
  const check = { method: 'decisions_api' as const, status: 'live' as const }
  it('says nothing when the response does not report check cost', () => {
    expect(checkParts(base, 'en')).toEqual([])
    expect(checkParts({ ...base, check }, 'en')).toEqual([])
  })
  it('shows a live check with requests, tokens and cost, apart from the answer', () => {
    const r = { ...base, check: { ...check, calls: 1, cost_usd: 0.0014, tokens: 13612, cached: false } }
    expect(footerParts(r, 'en')).toEqual(
      expect.arrayContaining(['0 model calls', '$0.00', 'check: 1 request', '13.6k tokens', '$0.0014']),
    )
  })
  it('shows a cached check as cached at $0.00', () => {
    const r = { ...base, check: { ...check, calls: 0, cost_usd: 0, tokens: 0, cached: true } }
    expect(checkParts(r, 'en')).toEqual(['check: cached', '$0.00'])
    expect(checkParts(r, 'fr')[0]).toBe('vérification: en cache')
  })
})

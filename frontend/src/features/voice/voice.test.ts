import { describe, expect, it } from 'vitest'
import type { AskResponse } from '@/api/contract'
import { answerFacts } from './askBlueprint'

const base = {
  contract_version: '1.2',
  detected_lang: 'en',
  canonical_en: 'how many windows',
  scope: { kind: 'drawing' },
  trace: [],
  calls: 0,
  cost_usd: 0,
  latency_ms: 0,
} as unknown as Omit<AskResponse, 'answer' | 'evidence' | 'method'>

describe('answerFacts (what the live co-pilot may say)', () => {
  it('passes the verified count, its group breakdown, method and check', () => {
    const r = {
      ...base,
      answer: { kind: 'count', value: 3, subject: 'window' },
      method: 'cad_geometry',
      evidence: {
        boxes: [
          { id: 'a', x: 0, y: 0, w: 1, h: 1, subject: 'window', group_id: 'g1' },
          { id: 'b', x: 0, y: 0, w: 1, h: 1, subject: 'window', group_id: 'g1' },
          { id: 'c', x: 0, y: 0, w: 1, h: 1, subject: 'window', group_id: 'g2' },
        ],
        rooms: [],
        groups: [
          { id: 'g1', label: 'Apartment A', color: 'c1' },
          { id: 'g2', label: 'Apartment B', color: 'c2' },
        ],
      },
      check: { method: 'decisions_api', status: 'live', agreed: 3, total: 3 },
    } as AskResponse
    expect(answerFacts(r)).toEqual({
      verified: true,
      method: 'exact, from the CAD geometry',
      answer: { count: 3, of: 'window' },
      breakdown: [
        { group: 'Apartment A', count: 2 },
        { group: 'Apartment B', count: 1 },
      ],
      caveats: ['independent check agreed on 3 of 3'],
    })
  })

  it('gives no number for an abstention', () => {
    const r = {
      ...base,
      answer: { kind: 'abstain', reason: 'not_on_drawing' },
      method: 'none',
      evidence: { boxes: [], rooms: [] },
    } as AskResponse
    const f = answerFacts(r)
    expect(f.answer).toBeNull()
    expect(f.no_answer).toBe('not_on_drawing')
  })
})

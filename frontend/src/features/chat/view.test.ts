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

import { chips } from '@/features/chat/view'
import { deriveAnswer, emptyToggles } from '@/lib/answer'

describe('check chip', () => {
  const base = askLocalForFooter({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })
  const d = deriveAnswer(base, emptyToggles())
  it('a DXF upload with no image says so instead of a sample check', () => {
    const r = { ...base, check: { method: 'decisions_api' as const, status: 'sample' as const, image: 'none' as const } }
    expect(chips(r, d, 'en').find((c) => c.key === 'check')?.text).toBe('No independent image to check against')
  })
  it('a ranked check names the least certain box by its number and has no agree count', () => {
    const r = {
      ...base,
      check: { method: 'decisions_api' as const, status: 'live' as const, scope: 'drawn_boxes' as const, ranking: ['win-09', 'win-01'], least_certain_id: 'win-09', agreed: 12, total: 12 },
    }
    const chip = chips(r, d, 'en').find((c) => c.key === 'check')!
    expect(chip.text).toBe('Gemini checked 2 drawn boxes · least certain #9')
    expect(chip.evidenceId).toBe('win-09')
    expect(chip.text).not.toMatch(/agree/)
  })
})

import { valueParts, sentence } from '@/features/chat/view'
import { isAskResponse, isRewriteResponse, isScope } from '@/api/contract'
import { bundledDrawing } from '@/data/drawing'

describe('1.1 answer kinds', () => {
  const base = askLocalForFooter({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })
  it('a measured IFC quantity is shown as given, with its unit, and validates', () => {
    const r = { ...base, answer: { kind: 'quantity' as const, value: 42.5, unit: 'm3' as const }, scope: { kind: 'model' as const } }
    expect(isAskResponse(r)).toBe(true)
    const d = deriveAnswer(r, emptyToggles())
    expect(valueParts(r, d, 'en')).toEqual(['42.50', 'm³'])
    expect(sentence(r, d, bundledDrawing, 'en')).toContain('in the whole model')
  })
  it('a text answer shows the printed text itself', () => {
    const r = { ...base, answer: { kind: 'text' as const, value: 'EI 30', subject: 'fire_rating' as const } }
    expect(valueParts(r, deriveAnswer(r, emptyToggles()), 'en')).toEqual(['EI 30', 'fire rating'])
  })
  it('evidence rows and the model scope validate; bad rows do not', () => {
    expect(isScope({ kind: 'model' })).toBe(true)
    const rows = [{ id: '2O2Fr$t4X7Zf8NOew3FLOH', name: 'Wall 1', value: 3.2 }]
    expect(isAskResponse({ ...base, evidence: { ...base.evidence, rows } })).toBe(true)
    expect(isAskResponse({ ...base, evidence: { ...base.evidence, rows: [{ name: 'x' }] } })).toBe(false)
  })
  it('a /rewrite response validates', () => {
    const r = {
      contract_version: '1.1',
      drawing_id: 'swiss-46639',
      detected_lang: 'en',
      blocks: [{ question: 'How many washbasins?', intent: 'count', subject: 'washbasin', scope: { kind: 'floor' }, scope_exists: true, routable: true }],
      not_answerable: ['is the ceiling high enough for a loft bed'],
      calls: 1,
      cost_usd: 0.0001,
      tokens: 900,
      model: 'gemini-flash-lite',
    }
    expect(isRewriteResponse(r)).toBe(true)
    expect(isRewriteResponse({ ...r, blocks: [{ ...r.blocks[0], scope: { kind: 'planet' } }] })).toBe(false)
  })
})

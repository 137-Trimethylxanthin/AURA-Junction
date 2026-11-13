import { describe, expect, it } from 'vitest'
import { isAskResponse, type AskRequest, type AskResponse } from './contract'
import { CONFORMANCE, MOCK_REGION } from './conformance.fixtures'
import { ask } from './local'
import { bundledDrawing } from '@/data/drawing'
import { deriveAnswer, emptyToggles, roomEvidenceId } from '@/lib/answer'
import { boxCentreInRect } from '@/lib/geometry'

const ID = 'swiss-46639'
const req = (question: string, extra: Partial<AskRequest> = {}): AskRequest => ({ drawing_id: ID, question, ...extra })

describe('conformance table (local ask)', () => {
  for (const c of CONFORMANCE) {
    it(c.name, () => {
      const r = ask(req(c.question, { scope: c.scope, overrides: c.overrides }))
      expect(isAskResponse(r)).toBe(true)
      expect(r.answer.kind).toBe(c.expect.kind)
      if (c.expect.kind === 'abstain') {
        expect(r.answer).toMatchObject({ kind: 'abstain', reason: c.expect.reason })
        expect(r.evidence.boxes).toHaveLength(0)
        expect(r.method).toBe('none')
      } else {
        expect(r.answer).toMatchObject(c.expect)
      }
      if (c.lang) expect(r.detected_lang).toBe(c.lang)
      if (c.gold === null) expect(r.gold).toBeUndefined()
      else if (c.gold) {
        expect(r.gold).toMatchObject(c.gold)
      }
      expect(r).toMatchObject({ calls: 0, cost_usd: 0, source: 'local' })
    })
  }
})

describe('local ask details', () => {
  it('area evidence lists every room of the scope, outdoor ones left out', () => {
    const r = ask(req('What is the net floor area of apartment A?'))
    expect(r.evidence.rooms).toHaveLength(13)
    expect(r.evidence.rooms.filter((x) => !x.included).map((x) => x.room_id)).toEqual(['3.01', '3.13'])
  })

  it('A + terrace 3.01 toggled in → 169.73 via answer.ts', () => {
    const r = ask(req('Quelle est la surface nette de l’appartement A en m² ?'))
    const t = emptyToggles()
    t.included[roomEvidenceId('3.01')] = true
    const d = deriveAnswer(r, t, bundledDrawing)
    expect(d.value).toBe(169.73)
    expect(d.gold.state).toBe('user_changed')
  })

  it('what-if marks the room as edited with area = w × h rounded', () => {
    const r = ask(req('What is the net floor area of apartment A?', { overrides: [{ room_id: '3.04', w_m: 4.135, h_m: 3.6 }] }))
    expect(r.evidence.rooms.find((x) => x.room_id === '3.04')).toMatchObject({ area_m2: 14.89, edited: true })
    expect(r.canonical_en).toContain('room 3.04')
  })

  it('region count equals boxes whose centre is inside the rect', () => {
    const scope = { kind: 'region' as const, rect: MOCK_REGION, label: 'Area 1' }
    const r = ask(req('How many doors here?', { scope }))
    const expected = bundledDrawing.evidence.door.filter((b) => boxCentreInRect(b, MOCK_REGION)).map((b) => b.id)
    expect(r.answer).toMatchObject({ kind: 'count', subject: 'door', value: expected.length })
    expect(r.evidence.boxes.map((b) => b.id)).toEqual(expected)
  })

  it('canonical_en for the windows question', () => {
    expect(ask(req('Wie viele Fenster hat die Wohnung A?')).canonical_en).toBe('How many windows are drawn in apartment A')
  })

  it('abstains carry where_to_find in the question language', () => {
    const r = ask(req('Quelle est la résistance au feu de la porte d’entrée ?'))
    expect(r.answer).toMatchObject({ kind: 'abstain', reason: 'not_on_drawing' })
    expect(r.detected_lang).toBe('fr')
    expect(r.answer.kind === 'abstain' && r.answer.where_to_find).toMatch(/IFC/)
  })

  it('rejects other drawings', () => {
    expect(() => ask({ drawing_id: 'other', question: 'How many windows?' })).toThrow()
  })
})

describe('integrity', () => {
  const all: AskResponse[] = CONFORMANCE.map((c) => ask(req(c.question, { scope: c.scope, overrides: c.overrides })))

  it('every trace confidence is "sample" or absent, and check has no invented numbers', () => {
    for (const r of all) {
      for (const n of r.trace) if (n.confidence !== undefined) expect(n.confidence).toBe('sample')
      if (r.check) expect(r.check).toEqual({ method: 'decisions_api', status: 'sample' })
    }
  })

  it('evidence ids are unique and stable across two calls', () => {
    for (const c of CONFORMANCE) {
      const a = ask(req(c.question, { scope: c.scope, overrides: c.overrides }))
      const b = ask(req(c.question, { scope: c.scope, overrides: c.overrides }))
      const ids = a.evidence.boxes.map((x) => x.id)
      expect(new Set(ids).size).toBe(ids.length)
      expect({ ...a, latency_ms: 0 }).toEqual({ ...b, latency_ms: 0 })
    }
    const allIds = Object.values(bundledDrawing.evidence).flatMap((l) => l.map((b) => b.id))
    expect(new Set(allIds).size).toBe(allIds.length)
  })

  it('every box lies inside the 0–100 % frame', () => {
    for (const b of Object.values(bundledDrawing.evidence).flat()) {
      expect(b.x).toBeGreaterThanOrEqual(0)
      expect(b.y).toBeGreaterThanOrEqual(0)
      expect(b.x + b.w).toBeLessThanOrEqual(100)
      expect(b.y + b.h).toBeLessThanOrEqual(100)
    }
  })

  it('trace evidence ids are a subset of the response evidence', () => {
    for (const r of all) {
      const known = new Set([...r.evidence.boxes.map((b) => b.id), ...r.evidence.rooms.map((x) => roomEvidenceId(x.room_id))])
      for (const n of r.trace) for (const id of n.evidence_ids ?? []) expect(known.has(id)).toBe(true)
    }
  })

  it('double doors carry a note', () => {
    expect(bundledDrawing.evidence.door.filter((d) => d.note)).toHaveLength(4)
  })
})

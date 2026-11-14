import { describe, expect, it } from 'vitest'
import {
  CONTRACT_VERSION,
  isAskResponse,
  isDrawing,
  isDrawingList,
  isDrawingStatusBody,
  isErrorBody,
  isRankedCheck,
  isUploadResult,
  majorOf,
} from './contract'
import { ask } from './local'
import { bundledDrawing as DRAWING } from '@/data/drawing'

const base = () => ask({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })

describe('contract 1.1 (additive)', () => {
  it('is version 1.1 and still major 1', () => {
    expect(CONTRACT_VERSION).toBe('1.1')
    expect(majorOf(CONTRACT_VERSION)).toBe('1')
  })

  it('accepts a 1.0 AskResponse and the 1.1 optional fields', () => {
    const r = base()
    expect(isAskResponse({ ...r, contract_version: '1.0' })).toBe(true)
    const r11 = {
      ...r,
      answer_id: 'a1',
      conversation_id: 'c1',
      turn_id: 't1',
      route: 'template',
      explanation: 'Counted from the CAD window layer.',
      check: {
        method: 'decisions_api',
        status: 'live',
        agreed: 11,
        total: 12,
        per_box: { 'win-01': 0.9 },
        scope: 'drawn_boxes',
        ranking: ['win-03', 'win-01'],
        least_certain_id: 'win-03',
        anchors_max: 0.2,
        image: 'dataset_png',
      },
      trace: [
        ...r.trace,
        {
          id: 'n9',
          title: 'count_objects',
          tag: 'tool',
          body: '',
          kind: 'exact',
          tool: { name: 'count_objects', args: { subject: 'window' }, result_id: 'r1', ms: 3 },
        },
      ],
    }
    expect(isAskResponse(r11)).toBe(true)
    expect(isAskResponse({ ...r11, route: 'guess' })).toBe(false)
    expect(isAskResponse({ ...r11, check: { ...r11.check, scope: 'everything' } })).toBe(false)
    expect(isAskResponse({ ...r11, trace: [{ ...r11.trace.at(-1)!, tool: { name: 'rm_rf', args: {}, result_id: 'x', ms: 1 } }] })).toBe(false)
  })

  it('accepts the text answer kind', () => {
    const r = { ...base(), answer: { kind: 'text', value: 'EI 30', subject: 'fire_rating' } }
    expect(isAskResponse(r)).toBe(true)
    expect(isAskResponse({ ...r, answer: { kind: 'text', value: 'EI 30', subject: 'colour' } })).toBe(false)
  })

  it('accepts Drawing 1.1 fields and rejects wrong ones', () => {
    expect(isDrawing(DRAWING)).toBe(true)
    const d = { ...DRAWING, kind: 'cad_dxf', status: 'ready', capabilities: ['window', 'door'], source: 'library', facts: { gold_total: 38, correct: 38, measured_at: '2026-11-14' } }
    expect(isDrawing(d)).toBe(true)
    expect(isDrawing({ ...d, kind: 'pdf' })).toBe(false)
    expect(isDrawing({ ...d, capabilities: ['toaster'] })).toBe(false)
    expect(isDrawing({ ...d, facts: { correct: 1 } })).toBe(false)
  })

  it('validates the new endpoint bodies', () => {
    const summary = {
      id: 'up-1',
      title: 'plan.dxf',
      kind: 'cad_dxf',
      status: 'processing',
      progress: 0.4,
      thumbnail: '',
      credit: { text: 'Your upload', license: 'yours' },
    }
    expect(isDrawingList({ contract_version: '1.1', drawings: [summary] })).toBe(true)
    expect(isDrawingList({ contract_version: '1.1', drawings: [{ ...summary, status: 'done' }] })).toBe(false)
    expect(isDrawingStatusBody({ contract_version: '1.1', id: 'up-1', status: 'failed', error: 'No entities' })).toBe(true)
    expect(isUploadResult({ drawing_id: 'up-1', delete_token: 'tok', status: 'queued' })).toBe(true)
    expect(isUploadResult({ drawing_id: 'up-1', status: 'queued' })).toBe(false)
    expect(isRankedCheck({ method: 'decisions_api', status: 'live', scope: 'drawn_boxes', ranking: [] })).toBe(true)
    expect(isRankedCheck({ method: 'decisions_api', status: 'live' })).toBe(false)
  })

  it('knows the 1.1 error codes', () => {
    for (const code of ['too_large', 'unsupported_type', 'rate_limited', 'budget_exhausted', 'presenter_required', 'processing'])
      expect(isErrorBody({ contract_version: '1.1', error: 'x', code })).toBe(true)
    expect(isErrorBody({ contract_version: '1.1', error: 'x', code: 'nope' })).toBe(false)
  })
})

describe('null as a value', () => {
  it('dropNulls keeps area_m2: null (unknown area) and drops other nulls', async () => {
    const { dropNulls } = await import('./client')
    expect(dropNulls({ a: null, rooms: [{ room_id: '1', included: false, area_m2: null, edited: null }] })).toEqual({
      rooms: [{ room_id: '1', included: false, area_m2: null }],
    })
  })
})

describe('safeUrl', () => {
  it('passes http(s) and root-relative URLs only', async () => {
    const { safeUrl } = await import('./contract')
    expect(safeUrl('https://doi.org/10.5281/zenodo.7788422')).toBe('https://doi.org/10.5281/zenodo.7788422')
    expect(safeUrl('/drawings/x/plan.svg')).toBe('/drawings/x/plan.svg')
    for (const bad of ['javascript:alert(1)', ' JavaScript:alert(1)', 'data:text/html,x', '//evil.test/x', 'vbscript:x', ''])
      expect(safeUrl(bad)).toBeNull()
  })
})

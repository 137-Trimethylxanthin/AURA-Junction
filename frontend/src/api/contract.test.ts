import { describe, expect, it } from 'vitest'
import {
  CHAT_EVENTS,
  CONTRACT_VERSION,
  GROUP_COLORS,
  groupColorVars,
  isAskResponse,
  isDrawing,
  isDrawingList,
  isDrawingStatusBody,
  isErrorBody,
  isEvidenceGroup,
  isEvidenceLink,
  isHealth,
  isProjectBundle,
  isProjectList,
  isRankedCheck,
  isScope,
  isUploadResult,
  majorOf,
  salvageAskResponse,
} from './contract'
import { ask } from './local'
import { bundledDrawing as DRAWING } from '@/data/drawing'

const base = () => ask({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })

describe('contract 1.2 (additive)', () => {
  it('is version 1.2 and still major 1', () => {
    expect(CONTRACT_VERSION).toBe('1.2')
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

  it('accepts the text and 1.2 note/agent_estimate answer kinds', () => {
    const r = { ...base(), answer: { kind: 'text', value: 'EI 30', subject: 'fire_rating' } }
    expect(isAskResponse(r)).toBe(true)
    expect(isAskResponse({ ...r, answer: { kind: 'text', value: 'EI 30', subject: 'colour' } })).toBe(false)
    expect(
      isAskResponse({
        ...base(),
        method: 'agent_estimate',
        answer: { kind: 'note', value: 'Room 3.04 has 3 windows', number: 3, unit: 'windows' },
        trace: [{ id: 'a1', title: 'deep_agent', tag: 'tool', body: 'ok', kind: 'model', tool: { name: 'deep_agent', args: {}, result_id: 'r1', ms: 120 } }],
      }),
    ).toBe(true)
    expect(isAskResponse({ ...base(), answer: { kind: 'note', value: 123 as unknown as string } })).toBe(false)
  })

  it('accepts Drawing 1.1 fields and rejects wrong ones', () => {
    expect(isDrawing(DRAWING)).toBe(true)
    const d = { ...DRAWING, kind: 'cad_dxf', status: 'ready', capabilities: ['window', 'door'], source: 'library', facts: { gold_total: 38, correct: 38, measured_at: '2026-11-14' } }
    expect(isDrawing(d)).toBe(true)
    expect(isDrawing({ ...d, kind: 'raster', source_ext: '.pdf' })).toBe(true)
    expect(isDrawing({ ...d, source_ext: 42 as unknown as string })).toBe(false)
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
    expect(isDrawingList({ contract_version: '1.2', drawings: [summary, { ...summary, id: 'up-2', kind: 'raster', source_ext: '.pdf' }] })).toBe(true)
    expect(isDrawingList({ contract_version: '1.1', drawings: [{ ...summary, status: 'done' }] })).toBe(false)
    expect(isDrawingStatusBody({ contract_version: '1.1', id: 'up-1', status: 'failed', error: 'No entities' })).toBe(true)
    expect(isUploadResult({ drawing_id: 'up-1', delete_token: 'tok', status: 'queued' })).toBe(true)
    expect(isUploadResult({ drawing_id: 'up-1', status: 'queued' })).toBe(false)
    expect(isRankedCheck({ method: 'decisions_api', status: 'live', scope: 'drawn_boxes', ranking: [] })).toBe(true)
    expect(isRankedCheck({ method: 'decisions_api', status: 'live' })).toBe(false)
    expect(CHAT_EVENTS).toContain('progress')
    expect(isHealth({ ok: true, contract_version: '1.2', answerer: 'hybrid', agent_ok: true })).toBe(true)
    expect(isHealth({ ok: true, contract_version: '1.2', answerer: 'bogus' })).toBe(false)
  })

  it('validates 1.2 color-coded EvidenceGroup, EvidenceLink, ScopeProject, and ProjectBundle', () => {
    expect(GROUP_COLORS).toEqual(['c1', 'c2', 'c3', 'c4', 'c5', 'c6'])
    expect(groupColorVars('c2')).toEqual({ fg: 'var(--group-2)', surface: 'var(--group-2-surface)', index: 2 })

    expect(isEvidenceGroup({ id: 'unit-A', label: 'Apartment A', color: 'c1' })).toBe(true)
    expect(isEvidenceGroup({ id: 'stair-1', label: 'Stair S1 → 1. OG', color: 'c6', target_drawing_id: 'd-1og' })).toBe(true)
    expect(isEvidenceGroup({ id: 'bad', label: 'Bad', color: 'c7' })).toBe(false)

    expect(
      isEvidenceLink({
        id: 'lnk-1',
        from_pos: [40, 50],
        to_pos: [40, 35],
        waypoints: [[40, 42]],
        group_id: 'stair-1',
        label: '→ 1. OG',
        drawing_id: 'd-eg',
        target_drawing_id: 'd-1og',
      }),
    ).toBe(true)
    expect(isEvidenceLink({ id: 'lnk-bad', from_pos: [40], to_pos: [40, 35] })).toBe(false)

    expect(isScope({ kind: 'project', project_id: 'proj-1', drawing_ids: ['d-eg', 'd-1og'] })).toBe(true)
    expect(isScope({ kind: 'project', project_id: 'proj-1', drawing_ids: [] })).toBe(false)
    expect(isScope({ kind: 'project', project_id: '', drawing_ids: ['d-eg'] })).toBe(false)

    const r = base()
    const multiFloorResp = {
      ...r,
      scope: { kind: 'project', project_id: 'proj-1', drawing_ids: ['swiss-46639', 'swiss-22518'] },
      evidence: {
        boxes: [{ ...r.evidence.boxes[0], group_id: 'unit-A', drawing_id: 'swiss-46639' }],
        rooms: [{ room_id: '3.04', included: true, area_m2: 17.04, group_id: 'unit-A', drawing_id: 'swiss-46639' }],
        groups: [{ id: 'unit-A', label: 'Apartment A', color: 'c1' }],
        links: [{ id: 'lnk-1', from_pos: [10, 20], to_pos: [15, 25], group_id: 'unit-A', drawing_id: 'swiss-46639' }],
      },
    }
    expect(isAskResponse(multiFloorResp)).toBe(true)

    // salvageAskResponse drops invalid group/link items while preserving valid ones
    const salvaged = salvageAskResponse({
      ...multiFloorResp,
      evidence: {
        ...multiFloorResp.evidence,
        groups: [...multiFloorResp.evidence.groups, { id: 'bad', label: 'Bad', color: 'c99' }],
        links: [...multiFloorResp.evidence.links, { id: 'bad-link', from_pos: [1] }],
      },
    }) as typeof multiFloorResp & { dropped_items?: number }
    expect(salvaged.dropped_items).toBe(2)
    expect(isAskResponse(salvaged)).toBe(true)

    expect(isDrawing({ ...DRAWING, project_id: 'proj-1', floor_label: '3e', floor_order: 3 })).toBe(true)
    expect(isDrawing({ ...DRAWING, floor_order: 'top' as unknown as number })).toBe(false)

    const bundle = {
      id: 'proj-1',
      title: 'Villa Nord',
      floors: [
        { drawing_id: 'd-kg', label: 'KG', order: -1 },
        { drawing_id: 'd-eg', label: 'EG', order: 0 },
      ],
      auto_matched: true,
    }
    expect(isProjectBundle(bundle)).toBe(true)
    expect(isProjectList({ contract_version: '1.2', projects: [bundle] })).toBe(true)
    expect(isProjectBundle({ ...bundle, floors: [{ drawing_id: '', label: 'EG', order: 0 }] })).toBe(false)
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

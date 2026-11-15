import { agentFailure, checkParts, footerParts } from '@/features/chat/view'
import type { AskResponse } from '@/api/contract'
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
    expect(checkParts(r, 'fr')[0]).toBe('contrôle: en cache')
  })
})

import { chips } from '@/features/chat/view'
import { deriveAnswer, emptyToggles } from '@/lib/answer'

describe('check chip', () => {
  const base = askLocalForFooter({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })
  const d = deriveAnswer(base, emptyToggles())
  it('a printed value is labelled as printed whoever read it; a measure on a scan is an estimate', () => {
    const d = deriveAnswer(base, emptyToggles())
    const area = { kind: 'area' as const, value: 20.25, unit: 'm2' as const }
    const printed = chips({ ...base, answer: area, method: 'vision', basis: 'printed' }, d, 'en')[0]
    expect(printed.text).toBe('Read from the sheet · as printed')
    expect(printed.tone).toBe('success')
    expect(printed.title).toContain('quoted as printed')
    expect(chips({ ...base, answer: area, method: 'vision', basis: 'estimate' }, d, 'en')[0].text).toBe('Estimate · measured on the scan')
    // exact geometry and answers without a basis (the bundles) keep their chips
    expect(chips({ ...base, method: 'cad_geometry', basis: 'measured' }, d, 'en')[0].text).toBe('Exact · CAD geometry')
    expect(chips({ ...base, answer: area, method: 'vision' }, d, 'en')[0].text).toBe('Model estimate · vision')
    expect(footerParts({ ...base, method: 'vision', basis: 'printed' }, 'de')[0]).toContain('vom Plan gelesen (wie gedruckt)')
  })
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

import { metricText, valueParts, sentence } from '@/features/chat/view'
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
  it('a printed or measured length explains itself: no "as given by the model file" line', () => {
    const r = {
      ...base,
      answer: { kind: 'quantity' as const, value: 24.384, unit: 'm' as const },
      explanation: 'Depth (up the page): 80\' - 0" as printed = 24.38 m (the overall dimension printed on the sheet).',
    }
    expect(isAskResponse(r)).toBe(true)
    const d = deriveAnswer(r, emptyToggles())
    expect(valueParts(r, d, 'en')).toEqual(['24.38', 'm'])
    expect(sentence(r, d, bundledDrawing, 'en')).toBe('')
    expect(sentence(r, d, { ...bundledDrawing, kind: 'ifc' }, 'en')).toContain('model file')
  })
  it('an answer asked in another unit shows that unit, with the metric value beside it', () => {
    const display = {
      unit: 'sqft', label: 'sq ft', style: 'decimal' as const,
      factors: { m: 3.280839895, m2: 10.76391042, m3: 35.31466672 },
      labels: { m: 'ft', m2: 'sq ft', m3: 'cu ft' },
      value: 3675.01, text: '3,675 sq ft', metric_text: '341.42 m²',
    }
    const area = { ...base, answer: { kind: 'area' as const, value: 341.42, unit: 'm2' as const }, display }
    expect(isAskResponse(area)).toBe(true)
    const d = deriveAnswer(area, emptyToggles())
    expect(valueParts(area, d, 'en')).toEqual(['3,675', 'sq ft'])
    expect(metricText(area, d, 'en')).toBe('341.42 m²')
    // measured under an estimated scale: both values say so, and the metric one has no false decimals
    const est = { ...area, answer: { ...area.answer, approximate: true } }
    expect(valueParts(est, d, 'en')).toEqual(['≈ 3,675', 'sq ft'])
    expect(metricText(est, d, 'en')).toBe('≈ 341 m²')
    const estNote = { ...base, answer: { kind: 'note' as const, value: 'OH: 22.30 m².', number: 22.3, unit: 'm2', approximate: true }, display: { ...display, value: 240.04, text: '240 sq ft', metric_text: '22.30 m²' } }
    const dn = deriveAnswer(estNote, emptyToggles())
    expect(valueParts(estNote, dn, 'en')).toEqual(['≈ 240', 'sq ft'])
    expect(metricText(estNote, dn, 'en')).toBe('≈ 22 m²')
    expect(valueParts({ ...estNote, display: undefined }, dn, 'de')).toEqual(['≈ 22', 'm² (Schätzung)'])
    const len = { ...base, answer: { kind: 'quantity' as const, value: 3.81, unit: 'm' as const }, display: { ...display, unit: 'ftin', label: 'ft-in', style: 'ft_in' as const } }
    expect(valueParts(len, deriveAnswer(len, emptyToggles()), 'en')).toEqual(["12' 6\"", ''])
    const note = { ...base, answer: { kind: 'note' as const, value: 'Wall 2400 mm.', number: 2400, unit: 'mm' }, display: { ...display, unit: 'ft', label: 'ft', value: 7.874, text: '7.87 ft', metric_text: '2,400 mm' } }
    expect(valueParts(note, deriveAnswer(note, emptyToggles()), 'en')).toEqual(['7.87', 'ft'])
    expect(metricText(note, deriveAnswer(note, emptyToggles()), 'en')).toBe('2,400 mm')
    // no display, or a malformed one: metric as before
    const bad = { ...area, display: { unit: 'sqft' } as unknown as typeof display }
    expect(valueParts(bad, d, 'en')).toEqual(['341.42', 'm²'])
    expect(metricText({ ...area, display: undefined }, d, 'en')).toBeNull()
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

import { rows as evidenceRows } from '@/features/chat/view'
import { licenceLabel } from '@/api/contract'

describe('"drawn twice" comes only from duplicate_of', () => {
  const base = askLocalForFooter({ drawing_id: 'swiss-46639', question: 'How many doors are drawn on this floor plan?' })
  it('the bundled double doors are labelled (both copies), other doors are not', () => {
    const titles = evidenceRows(base, emptyToggles(), bundledDrawing, 'en').map((r) => r.title)
    expect(titles.filter((x) => x.includes('drawn twice'))).toHaveLength(2)
    expect(titles.filter((x) => x.includes('second copy'))).toHaveLength(2)
    expect(titles.filter((x) => x.includes('CAD file'))).toHaveLength(4)
  })
  it('an IFC-style box with a note is a plain "Window 1" with the note as a neutral line', () => {
    const r = {
      ...base,
      answer: { kind: 'count' as const, value: 1, subject: 'window' as const },
      evidence: { boxes: [{ id: 'win-01', x: 10, y: 5, w: 16, h: 2, subject: 'window' as const, note: 'IFC 1TAI4ouKX4Xx4lBDZIu5qM' }], rooms: [] },
    }
    const [row] = evidenceRows(r, emptyToggles(), bundledDrawing, 'en')
    expect(row.title).toBe('Window 1')
    expect(row.note).toBe('IFC 1TAI4ouKX4Xx4lBDZIu5qM')
  })
  it('two boxes with identical rectangles but no duplicate_of are not called duplicates', () => {
    const box = { x: 10, y: 5, w: 16, h: 2, subject: 'window' as const, note: 'x' }
    const r = { ...base, evidence: { boxes: [{ ...box, id: 'win-01' }, { ...box, id: 'win-02' }], rooms: [] } }
    expect(evidenceRows(r, emptyToggles(), bundledDrawing, 'en').map((x) => x.title)).toEqual(['Window 1', 'Window 2'])
  })
})

describe('licence label', () => {
  it('shows the human name, an SPDX id as is, and never a LicenseRef id', () => {
    expect(licenceLabel({ license: 'CC BY 4.0' })).toBe('CC BY 4.0')
    expect(licenceLabel({ license: 'LicenseRef-KIT-unrestricted', license_name: 'KIT IFC Examples: for unrestricted use' })).toBe('KIT IFC Examples: for unrestricted use')
    expect(licenceLabel({ license: 'LicenseRef-KIT-unrestricted' })).toBe('Licence terms')
  })
})

describe('IFC counts with rows and no boxes', () => {
  const base = askLocalForFooter({ drawing_id: 'swiss-46639', question: 'How many doors are drawn on this floor plan?' })
  const rowsOnly = {
    ...base,
    answer: { kind: 'count' as const, value: 46, subject: 'door' as const },
    gold: undefined,
    evidence: { boxes: [], rooms: [], rows: Array.from({ length: 46 }, (_, i) => ({ id: `guid-${i}` })) },
  }
  it('shows the answer value, never a recount of zero boxes', () => {
    const d = deriveAnswer(rowsOnly, emptyToggles())
    expect(d.value).toBe(46)
    expect(valueParts(rowsOnly, d, 'en')).toEqual(['46', 'doors'])
  })
  it('a user toggle elsewhere cannot turn a rows count into a box recount', () => {
    const t = emptyToggles()
    t.rejected['win-99'] = true
    expect(deriveAnswer(rowsOnly, t).value).toBe(46)
  })
})

describe('rooms with no printed area', () => {
  const base = askLocalForFooter({ drawing_id: 'swiss-46639', question: 'Net floor area of apartment A' })
  const room = base.evidence.rooms[0]
  const resp = { ...base, evidence: { ...base.evidence, rooms: [...base.evidence.rooms, { room_id: 'x-1', included: false, area_m2: null }] } }
  it('are listed apart, never summed as 0, and say so', () => {
    const d = deriveAnswer(resp, emptyToggles())
    expect(d.noArea.map((r) => r.room_id)).toEqual(['x-1'])
    expect(d.value).toBe(base.answer.kind === 'area' ? base.answer.value : null)
    const t = emptyToggles()
    t.included['room-x-1'] = true
    t.included[`room-${room.room_id}`] = true
    expect(deriveAnswer(resp, t).value).toBe(deriveAnswer(base, t).value)
  })
})

describe('a note whose number is an area', () => {
  const base = askLocalForFooter({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })
  it('writes m², and an estimated area without false decimals, like its evidence row', () => {
    const exact = { ...base, method: 'cad_geometry' as const, answer: { kind: 'note' as const, value: 'OH is the largest room: 11.95 m².', number: 11.95, unit: 'm2' } }
    expect(valueParts(exact, deriveAnswer(exact, emptyToggles()), 'en')).toEqual(['11.95', 'm²'])
    const est = { ...base, method: 'vision' as const, answer: { ...exact.answer, approximate: true } }
    expect(isAskResponse(est)).toBe(true)
    const d = deriveAnswer(est, emptyToggles())
    expect(valueParts(est, d, 'en')).toEqual(['≈ 12', 'm² (estimate)'])
    expect(sentence(est, d, bundledDrawing, 'en')).toContain('Estimate')
    expect(sentence(est, d, bundledDrawing, 'en')).not.toContain('Read from the drawing')
  })
})

describe('room rows of a drawing with an estimated scale', () => {
  it('mark each area approximate, as the headline does', () => {
    const base = askLocalForFooter({ drawing_id: 'swiss-46639', question: 'Net floor area of apartment A' })
    const id = base.evidence.rooms[0].room_id
    const drawing = { ...bundledDrawing, rooms: bundledDrawing.rooms.map((r) => (r.room_id === id ? { ...r, area_source: 'estimate' as const } : r)) }
    const all = evidenceRows(base, emptyToggles(), drawing, 'en').filter((r) => r.kind === 'room')
    const text = (r: (typeof all)[number]) => JSON.stringify(r)
    expect(text(all[0])).toMatch(/≈ \d+ m² \(estimate\)/)
    expect(all.slice(1).some((r) => text(r).includes('≈'))).toBe(false)
  })
})

import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, useLocation } from 'react-router'
import { countPhrase, noun } from '@/lib/format'
import {
  evidenceGroupsOf,
  filterSlashCommands,
  groupCssVars,
  resolveSlashCommand,
  scopeChipText,
  whereText,
} from '@/features/chat/view'
import { BoxCropThumb } from '@/features/chat/BoxCropThumb'
import { AgentCard } from '@/features/chat/AgentCard'
import { Composer } from '@/features/chat/Composer'
import { DrawingTitle } from '@/features/chat/Header'
import { useDraft } from '@/features/chat/draft'
import { actions, appStore, type AgentTurn } from '@/store'

describe('nouns for the raster subjects', () => {
  it('have singular and plural forms in every language', () => {
    expect(noun('sauna', 1, 'en')).toBe('sauna')
    expect(noun('balcony', 2, 'en')).toBe('balconies')
    expect(noun('kitchen', 2, 'de')).toBe('Küchen')
    expect(noun('bathroom', 2, 'fr')).toBe('salles de bains')
    expect(noun('bedroom', 3, 'fi')).toBe('makuuhuonetta')
    expect(countPhrase('storey', 1, 'fi')).toBe('1 kerros')
  })
})

describe('1.2 agent_estimate, AnswerNote, categorical groups, and multi-floor rows', () => {
  const base = askLocalForFooter({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })

  it('renders agent_estimate chip and upper-bound cost, never Exact · CAD geometry', () => {
    const resp = {
      ...base,
      contract_version: '1.2',
      method: 'agent_estimate' as const,
      route: 'agent' as const,
      calls: 5,
      cost_usd: 0.12,
      answer: { kind: 'note' as const, value: '0.78 m clear doorway width in room 3.02', number: 0.78, unit: 'm' },
      source: 'api' as const,
    }
    const d = deriveAnswer(resp, emptyToggles())
    expect(valueParts(resp, d, 'en')).toEqual(['0.78', 'm'])
    expect(valueParts(resp, d, 'de')).toEqual(['0,78', 'm'])
    expect(sentence(resp, d, bundledDrawing, 'en')).toBe('Estimated from the drawing by the blueprint agent.')

    const chipList = chips(resp, d, 'en')
    expect(chipList.find((c) => c.key === 'method')).toEqual({
      key: 'method',
      text: 'Model estimate · agent',
      tone: 'info',
    })
    expect(chipList.some((c) => c.text.includes('Exact'))).toBe(false)
    expect(chipList.find((c) => c.key === 'cost')?.text).toBe('≤ $0.12 (upper bound)')
    expect(footerParts(resp, 'en')).toContain('≤ $0.12 (upper bound)')
  })

  it('formats integer AnswerNote numbers without .00 unless area unit, and omits "Rooms included: 0" on unboxed area estimates', () => {
    const intNote = {
      ...base,
      contract_version: '1.2',
      method: 'agent_estimate' as const,
      answer: { kind: 'note' as const, value: '2 stair flights connect EG and 1. OG', number: 2, unit: 'stairs' },
    }
    expect(valueParts(intNote, deriveAnswer(intNote, emptyToggles()), 'en')).toEqual(['2', 'stairs'])

    const agentArea = {
      ...base,
      contract_version: '1.2',
      method: 'agent_estimate' as const,
      answer: { kind: 'area' as const, value: 84.5, unit: 'm2' as const },
      evidence: { boxes: [], rooms: [] },
    }
    const areaSentence = sentence(agentArea, deriveAnswer(agentArea, emptyToggles()), bundledDrawing, 'en')
    expect(areaSentence).not.toContain('Rooms included: 0')
    expect(areaSentence).toContain('Estimated from the drawing by the blueprint agent.')
  })

  it('maps ScopeProject, categorical EvidenceGroup tokens, leastCertain, and cross-floor labels on rows', () => {
    const projectScope = { kind: 'project' as const, project_id: 'proj-1', drawing_ids: ['swiss-46639', 'up-og1', 'up-og2'] }
    expect(whereText(projectScope, 'en')).toBe('across all 3 floors')
    expect(scopeChipText(projectScope, 'de')).toBe('Über alle 3 Geschosse')
    expect(whereText({ kind: 'project', project_id: 'proj-1', drawing_ids: [] }, 'en')).toBe('across all floors')
    expect(whereText({ kind: 'project', project_id: 'proj-1', drawing_ids: [] }, 'fr')).toBe('sur tous les étages')
    expect(groupCssVars('c2')).toEqual({
      solid: 'var(--group-2, #6d3ab7)',
      surface: 'var(--group-2-surface, #efe7fb)',
    })

    const groupedResp = {
      ...base,
      contract_version: '1.2',
      method: 'agent_estimate' as const,
      check: {
        method: 'decisions_api' as const,
        status: 'live' as const,
        scope: 'drawn_boxes' as const,
        ranking: ['stair-2', 'stair-1'],
        least_certain_id: 'stair-2',
      },
      evidence: {
        boxes: [
          { id: 'stair-1', x: 10, y: 20, w: 8, h: 12, subject: 'door' as const, group_id: 's1', drawing_id: 'swiss-46639' },
          { id: 'stair-2', x: 12, y: 22, w: 8, h: 12, subject: 'door' as const, group_id: 's1', drawing_id: 'up-og1' },
        ],
        rooms: [],
        groups: [{ id: 's1', label: 'Stair S1 → 1. OG', color: 'c3' as const, target_drawing_id: 'up-og1' }],
      },
    } as unknown as typeof base

    expect(evidenceGroupsOf(groupedResp)).toHaveLength(1)
    const floorMap = new Map([
      ['swiss-46639', 'EG'],
      ['up-og1', '1. OG'],
    ])
    const rList = evidenceRows(groupedResp, emptyToggles(), bundledDrawing, 'en', floorMap)
    expect(rList[0]).toMatchObject({
      id: 'stair-1',
      groupId: 's1',
      groupColor: 'c3',
      groupLabel: 'Stair S1 → 1. OG',
      drawingId: 'swiss-46639',
    })
    expect(rList[0].floorLabel).toBeUndefined()
    expect(rList[1]).toMatchObject({
      id: 'stair-2',
      leastCertain: true,
      groupColor: 'c3',
      drawingId: 'up-og1',
      floorLabel: '1. OG',
    })
  })
})

describe('1.2 Slash commands (/clear, /zoom, /color, filtering & Composer DOM execution)', () => {
  it('filters slash commands by prefix or alias and ignores multiline or non-slash inputs', () => {
    expect(filterSlashCommands('How many windows?')).toEqual([])
    expect(filterSlashCommands('/clear\nmore')).toEqual([])
    expect(filterSlashCommands('/').length).toBeGreaterThanOrEqual(12)
    expect(filterSlashCommands('/cl').map((c) => c.id)).toEqual(['clear'])
    expect(filterSlashCommands('/csv').map((c) => c.id)).toEqual(['export'])
    expect(filterSlashCommands('/?').map((c) => c.id)).toEqual(['help'])
    expect(filterSlashCommands('/co').map((c) => c.id)).toEqual(['color', 'compare'])
    expect(filterSlashCommands('/zoom 250').map((c) => c.id)).toEqual(['zoom'])
  })

  it('parses /clear, /zoom <100..450>, /color contextual prompts, and aliases cleanly', () => {
    expect(resolveSlashCommand('/clear', { hasTurns: true })).toEqual({ kind: 'client', id: 'clear' })
    expect(resolveSlashCommand('/new', { hasTurns: true })).toEqual({ kind: 'client', id: 'new' })
    expect(resolveSlashCommand('/history', { hasTurns: false })).toEqual({ kind: 'client', id: 'history' })
    expect(resolveSlashCommand('/reset', { hasTurns: false })).toEqual({ kind: 'client', id: 'clear' })
    expect(resolveSlashCommand('/csv', { hasTurns: true })).toEqual({ kind: 'client', id: 'export' })
    expect(resolveSlashCommand('/?', { hasTurns: false })).toEqual({ kind: 'client', id: 'help' })
    expect(resolveSlashCommand('/zoom 250', { hasTurns: false })).toEqual({ kind: 'client', id: 'zoom', zoom: 250 })
    expect(resolveSlashCommand('/zoom 999%', { hasTurns: false })).toEqual({ kind: 'client', id: 'zoom', zoom: 450 })
    expect(resolveSlashCommand('/zoom 40', { hasTurns: false })).toEqual({ kind: 'client', id: 'zoom', zoom: 100 })
    expect(resolveSlashCommand('/zoom', { hasTurns: false })).toEqual({ kind: 'client', id: 'fit' })
    expect(resolveSlashCommand('/zoom nope', { hasTurns: false })).toMatchObject({ kind: 'invalid', id: 'zoom' })

    // Smart contextual /color prompt: empty chat vs follow-up turn vs explicit [by] criteria
    expect(resolveSlashCommand('/color', { hasTurns: false })).toEqual({
      kind: 'prompt',
      id: 'color',
      prompt: 'How many apartments are there? Color-code each in different colors',
    })
    expect(resolveSlashCommand('/color', { hasTurns: true })).toEqual({
      kind: 'prompt',
      id: 'color',
      prompt: 'Color-code these into different colors',
    })
    expect(resolveSlashCommand('/color by room type', { hasTurns: true })).toEqual({
      kind: 'prompt',
      id: 'color',
      prompt: 'Color-code these into different colors by room type',
    })
    expect(resolveSlashCommand('/stairs', { hasTurns: false })).toEqual({
      kind: 'prompt',
      id: 'stairs',
      prompt: 'Show all stairs and where they lead to in different colors',
    })
  })

  it('mounts Composer, opens role="listbox" on "/", and executes /zoom 250, /color, and /clear', async () => {
    // Ensure ResizeObserver exists in jsdom
    if (typeof globalThis.ResizeObserver === 'undefined') {
      globalThis.ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
      } as unknown as typeof ResizeObserver
    }
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    actions().newConversation()
    actions().resetZoom()
    useDraft.getState().setText('')

    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    await act(async () => {
      root.render(createElement(MemoryRouter, null, createElement(Composer)))
    })

    // Type "/" into draft -> listbox opens with min-h-10 options
    await act(async () => {
      useDraft.getState().setText('/zo')
    })
    const listbox = host.querySelector('[role="listbox"][data-slash-menu]')
    expect(listbox).not.toBeNull()
    expect(host.querySelectorAll('[role="option"]').length).toBe(1)

    // Execute /zoom 250 via Enter keydown on textarea without making an API/chat turn
    await act(async () => {
      useDraft.getState().setText('/zoom 250')
    })
    const ta = host.querySelector('textarea')!
    await act(async () => {
      ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    })
    expect(appStore.getState().ui.zoom).toBe(250)
    expect(appStore.getState().turns).toHaveLength(0)
    expect(useDraft.getState().text).toBe('')

    // Execute /color on empty chat -> submits apartment color-coding question into turns
    await act(async () => {
      useDraft.getState().setText('/color')
    })
    await act(async () => {
      ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    })
    expect(appStore.getState().turns.length).toBeGreaterThan(0)
    expect(appStore.getState().turns[0]).toMatchObject({
      role: 'user',
      text: 'How many apartments are there? Color-code each in different colors',
    })

    // Execute /clear -> clears conversation turns immediately
    await act(async () => {
      useDraft.getState().setText('/clear')
    })
    await act(async () => {
      ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    })
    expect(appStore.getState().turns).toHaveLength(0)

    // Clicking a slash option in the listbox preserves typed arguments (/zoom 320 and /color by room type)
    await act(async () => {
      useDraft.getState().setText('/zoom 320')
    })
    const zoomOpt = host.querySelector('[data-slash-cmd="zoom"]')!
    expect(zoomOpt).not.toBeNull()
    await act(async () => {
      zoomOpt.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }))
    })
    expect(appStore.getState().ui.zoom).toBe(320)

    await act(async () => {
      useDraft.getState().setText('/color by room type')
    })
    const colorOpt = host.querySelector('[data-slash-cmd="color"]')!
    expect(colorOpt).not.toBeNull()
    await act(async () => {
      colorOpt.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }))
    })
    expect(appStore.getState().turns[0]).toMatchObject({
      role: 'user',
      text: 'Show and color-code items on this floor plan in different colors by room type',
    })

    // Guard /whatif in agent mode, and cancel What-If draft when switching to /select
    await act(async () => {
      actions().newConversation()
      actions().setAnswererMode('agent')
      useDraft.getState().setText('/whatif')
    })
    await act(async () => {
      ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    })
    expect(appStore.getState().ui.tool).not.toBe('whatif')

    await act(async () => {
      actions().setAnswererMode('hybrid')
      actions().startWhatIf('3.04')
      useDraft.getState().setText('/select')
    })
    await act(async () => {
      ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    })
    expect(appStore.getState().ui.tool).toBe('select')
    expect(appStore.getState().ui.whatIf.roomId).toBeNull()

    // Multi-floor project scope chip ("All 3 floors") + /all-floors toggle
    await act(async () => {
      actions().setActiveProject({
        id: 'proj-1',
        title: 'Haus Nord',
        floors: [
          { drawing_id: 'swiss-46639', label: 'EG', order: 0 },
          { drawing_id: 'up-og1', label: '1. OG', order: 1 },
          { drawing_id: 'up-og2', label: '2. OG', order: 2 },
        ],
      })
      useDraft.getState().setText('/all-floors')
    })
    await act(async () => {
      ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    })
    expect(appStore.getState().crossPlanScope).toBe(true)
    expect(host.querySelector('[data-project-scope-chip="proj-1"]')?.textContent).toContain('All 3 floors')

    await act(async () => {
      actions().setActiveProject(null)
      root.unmount()
    })
    host.remove()
  })
})

describe('1.2 BoxCropThumb, AgentCard, and DrawingTitle DOM verification', () => {
  const base = askLocalForFooter({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })

  it('renders 56x56 BoxCropThumb SVG viewBox crop, enforces minSide clamp, and hides when cross-floor drawing is absent', async () => {
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    const tinyCornerBox = { id: 'win-corner', x: 99, y: 99, w: 0.2, h: 0.2, subject: 'window' as const }

    await act(async () => {
      root.render(
        createElement(BoxCropThumb, {
          box: tinyCornerBox,
          drawing: bundledDrawing,
          on: true,
        }),
      )
    })
    const svg = host.querySelector('svg')
    expect(svg).not.toBeNull()
    const [vx, vy, vw, vh] = (svg!.getAttribute('viewBox') ?? '').split(' ').map(Number)
    const [W, H] = bundledDrawing.frame.px
    const expectedMin = Math.max(24, Math.min(W, H) * 0.06)
    expect(vw).toBeGreaterThanOrEqual(expectedMin - 0.01)
    expect(vx + vw).toBeLessThanOrEqual(W + 0.01)
    expect(vy + vh).toBeLessThanOrEqual(H + 0.01)

    // Cross-floor box without targetDrawing loaded returns null rather than cropping wrong floor
    await act(async () => {
      root.render(
        createElement(BoxCropThumb, {
          box: tinyCornerBox,
          drawing: bundledDrawing,
          boxDrawingId: 'other-floor-id',
        }),
      )
    })
    expect(host.querySelector('svg')).toBeNull()

    await act(async () => {
      root.unmount()
    })
    host.remove()
  })

  it('renders AgentCard neutral Stopped card, GroupLegend pills, self_confidence, and hides Reject on non-count agent_estimate', async () => {
    if (typeof globalThis.ResizeObserver === 'undefined') {
      globalThis.ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
      } as unknown as typeof ResizeObserver
    }
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)

    const cancelledTurn: AgentTurn = {
      id: 'a-stop',
      role: 'agent',
      replyTo: 'u-stop',
      status: 'error',
      error: { code: 'cancelled', message: 'Stopped. Nothing was answered.' },
      toggles: emptyToggles(),
      listOpen: false,
      showCanonical: false,
      feedback: null,
    }
    await act(async () => {
      root.render(createElement(MemoryRouter, null, createElement(AgentCard, { turn: cancelledTurn, lang: 'en' })))
    })
    expect(host.textContent).toContain('Stopped')
    expect(host.textContent).toContain('Stopped. Nothing was answered.')

    const noteResp = {
      ...base,
      contract_version: '1.2',
      method: 'agent_estimate' as const,
      calls: 3,
      cost_usd: 0.08,
      estimate: { confidence: 0.82 },
      answer: { kind: 'note' as const, value: 'Stair S1 leads to 1. OG.' },
      check: {
        method: 'decisions_api' as const,
        status: 'live' as const,
        scope: 'drawn_boxes' as const,
        ranking: ['b-1'],
        least_certain_id: 'b-1',
      },
      evidence: {
        boxes: [{ id: 'b-1', x: 20, y: 30, w: 10, h: 10, subject: 'door' as const, group_id: 'g-1' }],
        rooms: [],
        rows: [{ id: 'ifc-1', name: 'StairFlight 1' }],
        groups: [{ id: 'g-1', label: 'Stair S1 → 1. OG', color: 'c2' as const, target_drawing_id: 'up-og1' }],
      },
    } as unknown as typeof base
    const noteTurn: AgentTurn = {
      id: 'a-note',
      role: 'agent',
      replyTo: 'u-note',
      status: 'done',
      response: noteResp,
      toggles: emptyToggles(),
      listOpen: true,
      showCanonical: false,
      feedback: null,
    }
    await act(async () => {
      appStore.setState((s) => ({ ...s, turns: [noteTurn], ui: { ...s.ui, activeTurnId: 'a-note' } }))
      root.render(
        createElement(
          MemoryRouter,
          null,
          createElement(DrawingTitle),
          createElement(AgentCard, { turn: noteTurn, lang: 'en' }),
        ),
      )
    })
    expect(host.querySelector('h1')).not.toBeNull()
    expect(host.querySelector('[data-group-pill="g-1"]')?.textContent).toContain('Stair S1 → 1. OG')
    expect(host.querySelector('[data-box-crop="b-1"]')).not.toBeNull()
    expect(host.querySelector('[data-estimate]')?.textContent).toContain('Self-reported confidence: 82 %')
    // Non-count agent_estimate row does not render a Reject button
    expect(host.querySelector('button[aria-label^="Reject "]')).toBeNull()

    // Pending stream turn renders queuePosition and deep_agent step
    const streamingTurn: AgentTurn = {
      id: 'a-stream',
      role: 'agent',
      replyTo: 'u-stream',
      status: 'pending',
      stream: {
        phase: 'working',
        queuePosition: 2,
        evidence: { boxes: [], rooms: [] },
        nodes: [
          {
            id: 'ag-1',
            title: '',
            tag: 'AGENT',
            body: '',
            kind: 'model',
            tool: { name: 'deep_agent', args: { seq: 1, step: 'Cropping stairwell S1' }, result_id: 'r1', ms: 10 },
          },
        ],
        resolved: [],
      },
      toggles: emptyToggles(),
      listOpen: false,
      showCanonical: false,
      feedback: null,
    }
    await act(async () => {
      appStore.setState((s) => ({ ...s, turns: [streamingTurn] }))
      root.render(createElement(MemoryRouter, null, createElement(AgentCard, { turn: streamingTurn, lang: 'en' })))
    })
    expect(host.querySelector('[data-stream-queue="2"]')?.textContent).toContain('In queue (#2)')
    expect(host.textContent).toContain('Inspecting drawing (step 1)')
    expect(host.textContent).toContain('Cropping stairwell S1')

    await act(async () => {
      actions().newConversation()
      root.unmount()
    })
    host.remove()
  })

  it('pins GroupLegend on mouse enter+click without un-focusing, clears unpinned hover on leave, jumps to target floor member, and renders cached cross-floor BoxCropThumb', async () => {
    if (typeof globalThis.ResizeObserver === 'undefined') {
      globalThis.ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
      } as unknown as typeof ResizeObserver
    }
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)

    let currentPath = '/d/swiss-46639'
    function LocationProbe() {
      const loc = useLocation()
      currentPath = loc.pathname
      return null
    }

    const siblingDrawing = {
      ...bundledDrawing,
      id: 'up-og1',
      title: 'Haus · 1. OG',
    }

    const crossResp = {
      ...base,
      contract_version: '1.2',
      method: 'agent_estimate' as const,
      answer: { kind: 'note' as const, value: '2 apartments and 1 stairwell' },
      check: {
        method: 'decisions_api' as const,
        status: 'live' as const,
        scope: 'drawn_boxes' as const,
        ranking: ['stair-og1', 'stair-eg'],
        least_certain_id: 'stair-og1',
      },
      evidence: {
        boxes: [
          { id: 'apt-a-1', x: 10, y: 15, w: 8, h: 8, subject: 'door' as const, group_id: 'apt-A', drawing_id: 'swiss-46639' },
          { id: 'stair-eg', x: 30, y: 35, w: 8, h: 8, subject: 'door' as const, group_id: 'stair-1', drawing_id: 'swiss-46639' },
          { id: 'stair-og1', x: 32, y: 36, w: 8, h: 8, subject: 'door' as const, group_id: 'stair-1', drawing_id: 'up-og1' },
        ],
        rooms: [],
        groups: [
          { id: 'apt-A', label: 'Apartment A', color: 'c1' as const },
          { id: 'stair-1', label: 'Stair S1 → 1. OG', color: 'c3' as const, target_drawing_id: 'up-og1' },
        ],
      },
    } as unknown as typeof base

    const turn: AgentTurn = {
      id: 'a-groups',
      role: 'agent',
      replyTo: 'u-groups',
      status: 'done',
      response: crossResp,
      toggles: emptyToggles(),
      listOpen: true,
      showCanonical: false,
      feedback: null,
    }

    await act(async () => {
      appStore.setState((s) => ({
        ...s,
        turns: [turn],
        focusedGroupId: null,
        drawingById: { ...s.drawingById, 'swiss-46639': bundledDrawing, 'up-og1': siblingDrawing },
        ui: { ...s.ui, activeTurnId: 'a-groups', selectedEvidenceId: null, focusedGroupId: null },
      }))
      root.render(
        createElement(
          MemoryRouter,
          { initialEntries: ['/d/swiss-46639'] },
          createElement(LocationProbe),
          createElement(AgentCard, { turn, lang: 'en' }),
        ),
      )
    })

    // Cross-floor least-certain box (`stair-og1` on `up-og1`) renders its 56x56 SVG crop from cached `drawingById`!
    expect(host.querySelector('[data-box-crop="stair-og1"] svg')).not.toBeNull()

    const aptPill = host.querySelector<HTMLButtonElement>('[data-group-pill="apt-A"]')!
    expect(aptPill.getAttribute('aria-pressed')).toBe('false')

    const fireMousePointer = (el: HTMLElement, type: 'pointerover' | 'pointerout') => {
      const ev = new MouseEvent(type, { bubbles: true, cancelable: true })
      Object.defineProperty(ev, 'pointerType', { value: 'mouse' })
      el.dispatchEvent(ev)
    }

    // 1. Hover enter -> previews `apt-A` without setting aria-pressed; hover leave -> clears back to null!
    await act(async () => {
      fireMousePointer(aptPill, 'pointerover')
    })
    expect(appStore.getState().focusedGroupId).toBe('apt-A')
    expect(aptPill.getAttribute('aria-pressed')).toBe('false')

    await act(async () => {
      fireMousePointer(aptPill, 'pointerout')
    })
    expect(appStore.getState().focusedGroupId).toBeNull()

    // 2. Hover enter -> click -> hover leave -> pins `apt-A` (`aria-pressed="true"` and `focusedGroupId === 'apt-A'`)!
    await act(async () => {
      fireMousePointer(aptPill, 'pointerover')
    })
    await act(async () => {
      aptPill.click()
    })
    await act(async () => {
      fireMousePointer(aptPill, 'pointerout')
    })
    expect(appStore.getState().focusedGroupId).toBe('apt-A')
    expect(aptPill.getAttribute('aria-pressed')).toBe('true')

    // 3. Clicking cross-floor pill `stair-1` (`target_drawing_id: 'up-og1'`) navigates to `/d/up-og1` AND selects `stair-og1`!
    const stairPill = host.querySelector<HTMLButtonElement>('[data-group-pill="stair-1"]')!
    await act(async () => {
      fireMousePointer(stairPill, 'pointerover')
    })
    await act(async () => {
      stairPill.click()
    })
    expect(appStore.getState().focusedGroupId).toBe('stair-1')
    expect(currentPath).toBe('/d/up-og1')
    expect(appStore.getState().ui.selectedEvidenceId).toBe('stair-og1')

    await act(async () => {
      actions().newConversation()
      root.unmount()
    })
    host.remove()
  })
})

describe('agentFailure', () => {
  const failed = (extra: Record<string, unknown> = {}) =>
    ({
      answer: { kind: 'abstain', reason: 'low_confidence' },
      method: 'none',
      route: 'agent',
      trace: [{ id: 'look', title: 'Look for the data', tag: 'drawing', body: 'The deep agent service is unavailable.', kind: 'warn' }],
      ...extra,
    }) as unknown as AskResponse

  it('reads the backend label, for each failure kind', () => {
    for (const kind of ['unavailable', 'timeout', 'error'] as const) expect(agentFailure(failed({ agent_failure: kind }))).toBe(kind)
  })

  it('never parses the trace wording: no field, no failure card', () => {
    expect(agentFailure(failed())).toBeNull()
    expect(agentFailure(failed({ estimate: { confidence: 0.4 } }))).toBeNull()
  })

  it('the contract accepts the field and rejects unknown values', () => {
    const r = askLocalForFooter({ drawing_id: bundledDrawing.id, question: 'how many windows' })
    expect(isAskResponse({ ...r, agent_failure: 'timeout' })).toBe(true)
    expect(isAskResponse({ ...r, agent_failure: 'exploded' })).toBe(false)
  })
})

describe('note answers are labelled by their method, not by their kind', () => {
  // Real /ask payload (swiss-26874, "what is the biggest room"): an exact CAD note, 0 model calls.
  const note = (method: AskResponse['method']) =>
    ({
      contract_version: '1.2',
      answer: { kind: 'note', value: 'Séjour/repas (2.16) is the largest room: 35.43 m².', number: 35.43, unit: 'm2' },
      evidence: { boxes: [], rooms: [] },
      detected_lang: 'en',
      canonical_en: 'what is the biggest room',
      scope: { kind: 'floor' },
      method,
      trace: [],
      calls: 0,
      cost_usd: 0,
      latency_ms: 1,
      source: 'api',
      route: 'cache',
    }) as unknown as AskResponse

  it('an exact CAD note reads as exact everywhere on the card', () => {
    const r = note('cad_geometry')
    const d = deriveAnswer(r, emptyToggles())
    expect(chips(r, d, 'en')[0]).toMatchObject({ key: 'method', text: 'Exact · CAD geometry', tone: 'success' })
    expect(footerParts(r, 'en')[0]).toBe('answer: CAD geometry')
    expect(sentence(r, d, bundledDrawing, 'en')).toBe('Computed exactly from the CAD geometry.')
  })

  it('only an agent note is called a model estimate', () => {
    const r = note('agent_estimate')
    const d = deriveAnswer(r, emptyToggles())
    expect(chips(r, d, 'en')[0]).toMatchObject({ text: 'Model estimate · agent', tone: 'info' })
    expect(footerParts(r, 'en')[0]).toBe('answer: Model estimate · agent')
    expect(sentence(r, d, bundledDrawing, 'en')).toBe('Estimated from the drawing by the blueprint agent.')
  })
})

describe('pdf_vector and store_sql methods', () => {
  const base = askLocalForFooter({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })
  for (const [method, label] of [
    ['pdf_vector', 'Read from the PDF'],
    ['store_sql', 'Computed by SQL'],
  ] as const) {
    it(`${method} is accepted and rendered as an exact-style chip`, () => {
      const r = { ...base, method } as AskResponse
      expect(isAskResponse(r)).toBe(true)
      const d = deriveAnswer(r, emptyToggles())
      expect(chips(r, d, 'en')[0]).toMatchObject({ key: 'method', text: label, tone: 'success' })
      expect(footerParts(r, 'en')[0]).toBe(`answer: ${label}`)
    })
    it(`${method} with an estimate keeps the estimate styling`, () => {
      const r = { ...base, method, estimate: { confidence: 0.7 } } as AskResponse
      const d = deriveAnswer(r, emptyToggles())
      expect(chips(r, d, 'en')[0]).toMatchObject({ key: 'method', tone: 'neutral' })
    })
  }
})

import { announce, triedText } from '@/features/chat/view'

describe('not_answerable abstain', () => {
  const base = askLocalForFooter({ drawing_id: 'swiss-46639', question: 'How many windows are drawn on this floor plan?' })
  // Trace and answer node as returned by the live backend on swiss-4397 (walking-distance question).
  const r = {
    ...base,
    answer: { kind: 'abstain', reason: 'not_answerable' },
    method: 'none',
    evidence: { boxes: [], rooms: [] },
    trace: [
      { id: 'q', title: 'Question', tag: 'English · detected', body: '“distance by walking route”', kind: 'info' },
      {
        id: 'look',
        parent: 'q',
        title: 'Look for the data',
        tag: 'drawing',
        body: "I cannot answer this from the drawing's data: the tools found no answer within their limits.",
        kind: 'warn',
      },
      { id: 'answer', parent: 'q', title: 'Answer', tag: 'abstain', body: 'Abstained. Nothing guessed.', kind: 'warn' },
    ],
  } as unknown as AskResponse

  it('is accepted, announced neutrally, and reports what was tried', () => {
    expect(isAskResponse(r)).toBe(true)
    expect(announce(r, deriveAnswer(r, emptyToggles()))).toBe('Not in this drawing’s data. Nothing guessed.')
    expect(triedText(r)).toBe('the tools found no answer within their limits.')
    expect(triedText({ ...r, trace: [] })).toBeNull()
  })
})

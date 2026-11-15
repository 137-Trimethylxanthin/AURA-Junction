import { describe, expect, it } from 'vitest'
import type { Drawing, ProjectBundle } from '@/api/contract'
import { bundledDrawing } from '@/data/drawing'
import { PROMPT_BLOCKS, SUGGESTIONS, fillBlock, promptBlocksFor, suggestionFits, suggestionsFor, useDraft } from './draft'

const VALID_PB_LABELS = new Set(['Count', 'Area', 'Where is', 'Check', 'List'])

const ifcDrawing: Drawing = {
  ...bundledDrawing,
  id: 'ifc-kit-fzk-haus',
  kind: 'ifc',
  units: [],
  capabilities: ['door', 'window', 'room'],
  rooms: bundledDrawing.rooms.slice(0, 2).map((r, i) => ({ ...r, name: i === 0 ? 'Wohnen' : 'Flur', unit: undefined })),
}

const pdfDrawing: Drawing = {
  ...bundledDrawing,
  id: 'up-pdf-1',
  kind: 'raster',
  source_ext: '.pdf',
  units: [],
  rooms: [],
  capabilities: [],
}

describe('draft capability helpers', () => {
  it('returns exact SUGGESTIONS and PROMPT_BLOCKS when undefined or on bundled swiss-46639', () => {
    expect(suggestionsFor()).toBe(SUGGESTIONS)
    expect(suggestionsFor(bundledDrawing)).toBe(SUGGESTIONS)
    expect(promptBlocksFor()).toBe(PROMPT_BLOCKS)
    expect(promptBlocksFor(bundledDrawing)).toBe(PROMPT_BLOCKS)
  })

  it('adapts IFC suggestions and prompt blocks without unsupported apartment or washbasin queries', () => {
    const sugg = suggestionsFor(ifcDrawing)
    expect(sugg).toHaveLength(4) // no staircase among its rooms: no stair question
    expect(sugg.some((s) => /stairs/i.test(s.text))).toBe(false)
    expect(sugg.some((s) => /appartement|apartment/i.test(s.text))).toBe(false)
    expect(sugg.some((s) => /fire rating/i.test(s.text))).toBe(false)

    // a model with a staircase among its spaces is still not asked the stair questions (they abstained on hitos)
    const withStair = { ...ifcDrawing, rooms: [...ifcDrawing.rooms, { ...ifcDrawing.rooms[0], room_id: 'st', name: 'Trapp' }] }
    expect(suggestionFits({ lang: 'EN', text: 'Show all stairs and where they lead to', short: '' }, withStair)).toBe(false)
    expect(suggestionFits({ lang: 'EN', text: 'Match stairs and shafts across all floors', short: '' }, withStair)).toBe(false)
    expect(suggestionFits({ lang: 'EN', text: 'Show all stairs and where they lead to', short: '' }, { ...withStair, kind: 'raster' })).toBe(true)

    const blocks = promptBlocksFor(ifcDrawing)
    expect(blocks).toHaveLength(5)
    for (const b of blocks) {
      expect(VALID_PB_LABELS.has(b.label)).toBe(true)
      expect(b.text).not.toMatch(/washbasin|apartment/i)
    }
    expect(blocks.find((b) => b.label === 'Where is')?.text).toBe('Where is Wohnen?')
  })

  it('adapts PDF / raster suggestions and prompt blocks with valid PB_LABEL keys', () => {
    const scan: Drawing = {
      ...pdfDrawing,
      capabilities: ['door', 'window', 'room'],
      rooms: bundledDrawing.rooms.slice(0, 2).map((r, i) => ({ ...r, name: i === 0 ? 'OH' : 'Stair' })),
    }
    const sugg = suggestionsFor(scan)
    expect(sugg).toHaveLength(5)
    expect(sugg.some((s) => /appartement A|fire rating/i.test(s.text))).toBe(false)

    const blocks = promptBlocksFor(pdfDrawing)
    expect(blocks.map((b) => b.label)).toEqual(['Count', 'Area', 'Where is', 'Check', 'List'])
  })

  it('fillBlock populates useDraft text and selection range for the editable token', () => {
    const block = promptBlocksFor(ifcDrawing)[0]
    fillBlock(block)
    const st = useDraft.getState()
    expect(st.text).toBe('How many doors are on this storey?')
    expect(st.select).not.toBeNull()
    expect(st.text.slice(st.select![0], st.select![1])).toBe('doors')
  })

  it('includes color-coded apartment & stair flow suggestions and adapts multi-floor project bundles', () => {
    expect(SUGGESTIONS.some((s) => s.text === 'How many apartments are there?')).toBe(true)
    expect(SUGGESTIONS.some((s) => s.text === 'Show all stairs and where they lead to')).toBe(true)

    const bundle: ProjectBundle = {
      id: 'proj-1',
      title: 'Duplex House',
      floors: [
        { drawing_id: 'swiss-46639', label: '3e', order: 3 },
        { drawing_id: 'ifc-kit-fzk-haus', label: 'EG', order: 0 },
      ],
    }
    const projSugg = suggestionsFor(bundledDrawing, bundle)
    expect(projSugg[0].text).toBe('Match stairs and shafts across all floors')
    expect(projSugg[1].text).toBe('Show all stairs and where they lead to')
    expect(projSugg.some((s) => /apartments.*across all floors/i.test(s.text))).toBe(true)

    const ifcProjSugg = suggestionsFor(ifcDrawing, bundle)
    expect(ifcProjSugg.some((s) => /apartment/i.test(s.text))).toBe(false)

    const projBlocks = promptBlocksFor(bundledDrawing, bundle)
    expect(projBlocks.map((b) => b.label)).toEqual(['Count', 'Area', 'Where is', 'Check', 'List'])
    for (const b of projBlocks) expect(VALID_PB_LABELS.has(b.label)).toBe(true)
  })

  it('offers only questions the drawing can answer', () => {
    // a sheet with doors only (a site plan): no window, room, area or stair question
    const doorsOnly: Drawing = { ...pdfDrawing, kind: 'cad_dxf', capabilities: ['door'] }
    const d = suggestionsFor(doorsOnly).map((s) => s.text)
    expect(d).toEqual(['How many doors are shown on this floor plan?', 'What does this drawing show?'])

    // a scan with doors and windows but no rooms
    const noRooms: Drawing = { ...pdfDrawing, capabilities: ['door', 'window'] }
    const n = suggestionsFor(noRooms).map((s) => s.text)
    expect(n.some((t) => /huone|Raum|Räume|stairs/i.test(t))).toBe(false)
    expect(n.some((t) => /doors/.test(t))).toBe(true)
    expect(n.some((t) => /fenêtres/.test(t))).toBe(true)
    expect(n.filter((t) => /window|fenêtre/i.test(t))).toHaveLength(1) // not the same question twice

    // rooms but no staircase: no stair question; with a staircase (by type or by name) it is offered
    const rooms = bundledDrawing.rooms.slice(0, 2).map((r) => ({ ...r, name: 'Zimmer' }))
    const flat: Drawing = { ...pdfDrawing, capabilities: ['door', 'window', 'room'], rooms }
    expect(suggestionsFor(flat).some((s) => /stairs/i.test(s.text))).toBe(false)
    const typed = { ...flat, rooms: [{ ...rooms[0], name: 'TRH', room_type: 'staircase' }, rooms[1]] } as Drawing
    expect(suggestionsFor(typed).some((s) => /stairs/i.test(s.text))).toBe(true)
    for (const s of suggestionsFor(flat)) expect(suggestionFits(s, flat)).toBe(true)
  })

  it('asks about the whole building in the all-floors scope and never about "this storey"', () => {
    const bundle: ProjectBundle = {
      id: 'p',
      title: 'Model',
      floors: [
        { drawing_id: 'ifc-kit-fzk-haus', label: 'EG', order: 0 },
        { drawing_id: 'ifc-x', label: 'OG', order: 1 },
      ],
    }
    const all = suggestionsFor(ifcDrawing, bundle, true).map((s) => s.text)
    expect(all.length).toBeGreaterThanOrEqual(3)
    for (const t of all) expect(t).toMatch(/across all floors/)
    expect(all.some((t) => /apartment|stairs/i.test(t))).toBe(false)

    // a model without rooms or windows: only the door question, in both scopes
    const bare: Drawing = { ...ifcDrawing, rooms: [], capabilities: ['door'] }
    expect(suggestionsFor(bare, bundle, true).map((s) => s.text)).toEqual(['How many doors are there across all floors?'])
    const single = suggestionsFor(bare, bundle).map((s) => s.text)
    expect(single).toContain('How many doors are there across all floors?')
    expect(single.some((t) => /stairs|windows|surface|Räume/i.test(t))).toBe(false)
  })
})

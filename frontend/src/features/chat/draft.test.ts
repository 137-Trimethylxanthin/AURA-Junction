import { describe, expect, it } from 'vitest'
import type { Drawing, ProjectBundle } from '@/api/contract'
import { bundledDrawing } from '@/data/drawing'
import { PROMPT_BLOCKS, SUGGESTIONS, fillBlock, promptBlocksFor, suggestionsFor, useDraft } from './draft'

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
    expect(sugg).toHaveLength(5)
    expect(sugg.some((s) => /appartement|apartment/i.test(s.text))).toBe(false)
    expect(sugg.some((s) => /fire rating/i.test(s.text))).toBe(false)

    const blocks = promptBlocksFor(ifcDrawing)
    expect(blocks).toHaveLength(5)
    for (const b of blocks) {
      expect(VALID_PB_LABELS.has(b.label)).toBe(true)
      expect(b.text).not.toMatch(/washbasin|apartment/i)
    }
    expect(blocks.find((b) => b.label === 'Where is')?.text).toBe('Where is Wohnen?')
  })

  it('adapts PDF / raster suggestions and prompt blocks with valid PB_LABEL keys', () => {
    const sugg = suggestionsFor(pdfDrawing)
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
})

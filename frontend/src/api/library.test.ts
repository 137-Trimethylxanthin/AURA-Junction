import { beforeEach, describe, expect, it } from 'vitest'
import type { DrawingSummary } from './contract'
import { bundledDrawing } from '@/data/drawing'
import { BUNDLED_SUMMARY } from '@/data/ids'
import {
  apiUrl,
  autoGroupDrawings,
  checkFile,
  compactCredit,
  displayDrawingTitle,
  factsRatio,
  inferFloorOrderAndLabel,
  kindText,
  myProjects,
} from './library'

const MB = 1024 * 1024

describe('upload checks mirror the server caps', () => {
  it('accepts DXF (20 MB), PDF (20 MB) and IFC (60 MB) for everyone', () => {
    expect(checkFile({ name: 'A.DXF', size: 20 * MB }, false)).toEqual({ ok: true, type: 'dxf' })
    expect(checkFile({ name: 'a.dxf', size: 20 * MB + 1 }, false)).toMatchObject({ ok: false, code: 'too_large' })
    expect(checkFile({ name: 'a.dxf', size: 0 }, false)).toMatchObject({ ok: false, code: 'empty' })
    expect(checkFile({ name: 'sheet.pdf', size: 20 * MB }, false)).toEqual({ ok: true, type: 'pdf' })
    expect(checkFile({ name: 'sheet.pdf', size: 20 * MB + 1 }, false)).toMatchObject({ ok: false, code: 'too_large' })
    expect(checkFile({ name: 'model.ifc', size: 60 * MB }, false)).toEqual({ ok: true, type: 'ifc' })
    expect(checkFile({ name: 'model.ifc', size: 60 * MB + 1 }, false)).toMatchObject({ ok: false, code: 'too_large' })
  })
  it('PNG/JPG/WebP (15 MB) need presenter access', () => {
    expect(checkFile({ name: 'a.png', size: 10 }, false)).toMatchObject({ ok: false, code: 'presenter_required' })
    expect(checkFile({ name: 'a.jpeg', size: 15 * MB }, true)).toEqual({ ok: true, type: 'image' })
    expect(checkFile({ name: 'a.webp', size: 15 * MB }, true)).toEqual({ ok: true, type: 'image' })
    expect(checkFile({ name: 'a.jpg', size: 15 * MB + 1 }, true)).toMatchObject({ ok: false, code: 'too_large' })
  })
  it('refuses DWG, zip and unknown types while keeping the Only DXF prefix', () => {
    for (const name of ['a.dwg', 'a.zip', 'a', 'a.dxf.exe']) {
      const res = checkFile({ name, size: 10 }, true)
      expect(res).toMatchObject({ ok: false, code: 'unsupported_type' })
      if (!res.ok) expect(res.message).toMatch(/^Only DXF/)
    }
  })
})

describe('library display helpers', () => {
  it('displayDrawingTitle replaces generic Project Name while preserving real titles', () => {
    expect(displayDrawingTitle({ title: 'Plan 3e étage', id: 'swiss-46639' })).toBe('Plan 3e étage')
    expect(
      displayDrawingTitle({
        id: 'ifc-bimspeed-warmond_ar--2-level',
        title: 'Project Name · 2. Level',
        credit: {
          text: 'BIM-Speed Warmond demo site (baseline): Garcia et al.',
          license: 'CC-BY-4.0',
          license_name: 'CC BY 4.0',
        },
      }),
    ).toBe('BIM-Speed Warmond · 2. Level')
  })

  it('compactCredit condenses dataset paragraphs while preserving CC BY tokens', () => {
    expect(
      compactCredit({
        text: 'Swiss Dwellings v3.0.0 by Matthias Standfest et al. (Archilyse AG), doi:10.5281/zenodo.7788422, CC BY 4.0.',
        license: 'CC BY 4.0',
        license_name: 'CC BY 4.0',
      }),
    ).toBe('CC BY 4.0 · Swiss Dwellings v3.0.0')
    expect(compactCredit({ text: 'CubiCasa5k, CC BY-NC 4.0', license: 'CC BY-NC 4.0' })).toBe(
      'CC BY-NC 4.0 · CubiCasa5k',
    )
  })

  it('kindText and factsRatio format kinds and measured accuracy cleanly', () => {
    const mockTu = (k: 'kind_image' | 'kind_pdf') => (k === 'kind_pdf' ? 'PDF' : 'Image')
    expect(kindText('cad_dxf', mockTu)).toBe('CAD · DXF')
    expect(kindText('raster', mockTu)).toBe('Image')
    expect(kindText('raster', mockTu, '.pdf')).toBe('PDF')
    expect(factsRatio({ facts: { gold_total: 38, correct: 38, measured_at: '2026-11-14' } })).toBe('38/38')
    expect(factsRatio({})).toBeNull()
  })
})

describe('apiUrl', () => {
  it('resolves root-relative URLs against the API base only', () => {
    expect(apiUrl('/drawings/x/plan.svg', 'https://api.example/')).toBe('https://api.example/drawings/x/plan.svg')
    expect(apiUrl('https://cdn/x.png', 'https://api.example')).toBe('https://cdn/x.png')
    // Protocol-relative and non-http URLs from the API are refused (security review L8/XSS surfaces).
    expect(apiUrl('//cdn/x.png', 'https://api.example')).toBeNull()
    expect(apiUrl('javascript:alert(1)', 'https://api.example')).toBeNull()
    expect(apiUrl('/drawings/x/plan.svg', null)).toBe('/drawings/x/plan.svg')
  })
})

describe('BUNDLED_SUMMARY', () => {
  it('matches the bundled drawing file', () => {
    expect(BUNDLED_SUMMARY.title).toBe(bundledDrawing.title)
    expect(BUNDLED_SUMMARY.subtitle).toBe(bundledDrawing.subtitle)
    expect(BUNDLED_SUMMARY.credit).toEqual(bundledDrawing.credit)
    expect(BUNDLED_SUMMARY.thumbnail).toBe(bundledDrawing.images.rebuilt)
  })
})

describe('inferFloorOrderAndLabel', () => {
  it('orders basements, ground floors, upper storeys, attics and numbered pages across EN/FR/FI/DE', () => {
    expect(inferFloorOrderAndLabel('Villa_Mueller_2UG.dxf')).toEqual({ order: -2, label: '2. UG' })
    expect(inferFloorOrderAndLabel('Smiley West · KG')).toEqual({ order: -1, label: 'KG' })
    expect(inferFloorOrderAndLabel('house-b1.pdf')).toEqual({ order: -1, label: 'B1' })
    expect(inferFloorOrderAndLabel('Plan sous-sol')).toEqual({ order: -1, label: 'SS' })
    expect(inferFloorOrderAndLabel('Kellari')).toEqual({ order: -1, label: 'Kellari' })

    expect(inferFloorOrderAndLabel('Grundriss EG')).toEqual({ order: 0, label: 'EG' })
    expect(inferFloorOrderAndLabel('Plan rez-de-chaussée')).toEqual({ order: 0, label: 'RDC' })
    expect(inferFloorOrderAndLabel('Ground floor plan')).toEqual({ order: 0, label: 'GF' })
    expect(inferFloorOrderAndLabel('Pohjakerros')).toEqual({ order: 0, label: 'PK' })

    expect(inferFloorOrderAndLabel('Zwischengeschoss')).toEqual({ order: 0.5, label: 'MZ' })

    expect(inferFloorOrderAndLabel('Plan 1er étage')).toEqual({ order: 1, label: '1er' })
    expect(inferFloorOrderAndLabel('Grundriss 2. OG')).toEqual({ order: 2, label: '2. OG' })
    expect(inferFloorOrderAndLabel('Project Name · 2. Level')).toEqual({ order: 2, label: 'Level 2' })
    expect(inferFloorOrderAndLabel('Pianta 3° piano')).toEqual({ order: 3, label: '3°' })
    expect(inferFloorOrderAndLabel('4. kerros')).toEqual({ order: 4, label: '4. krs' })
    expect(inferFloorOrderAndLabel('Tower_5F.png')).toEqual({ order: 5, label: '5F' })

    expect(inferFloorOrderAndLabel('Villa_DG.dxf')).toEqual({ order: 99, label: 'DG' })
    expect(inferFloorOrderAndLabel('Roof plan')).toEqual({ order: 99, label: 'Roof' })
    expect(inferFloorOrderAndLabel('up-a1b2c3-p3')).toEqual({ order: 3, label: 'Page 3' })
    expect(inferFloorOrderAndLabel('Villa Aurora · up-abc999-p2')).toEqual({ order: 2, label: 'Page 2' })
    expect(inferFloorOrderAndLabel('up-3f')).toEqual({ order: 0, label: 'GF' })
    expect(inferFloorOrderAndLabel('Villa Nord · up-2f')).toEqual({ order: 0, label: 'GF' })
    expect(inferFloorOrderAndLabel('Villa Nord · up-b1')).toEqual({ order: 0, label: 'GF' })
    expect(inferFloorOrderAndLabel('Villa Nord · up-eg')).toEqual({ order: 0, label: 'GF' })
    expect(inferFloorOrderAndLabel('')).toEqual({ order: 0, label: 'GF' })
  })
})

const mkSummary = (id: string, title: string, extra: Partial<DrawingSummary> = {}): DrawingSummary => ({
  ...BUNDLED_SUMMARY,
  id,
  title,
  subtitle: undefined,
  ...extra,
})

describe('autoGroupDrawings & myProjects', () => {
  beforeEach(() => {
    myProjects.clear()
  })

  it('auto-groups explicit project_id, multi-page uploads, multi-storey IFCs and house stems in ascending floor order', () => {
    const drawings: DrawingSummary[] = [
      // Unrelated Swiss singletons with different Site IDs must NOT merge despite generic "Grundriss" titles
      mkSummary('swiss-15123', 'Grundriss EG', { subtitle: 'Site 2297 · 1:100' }),
      mkSummary('swiss-22513', 'Grundriss 3. OG', { subtitle: 'Site 3237 · 1:100' }),
      // Multi-storey IFC siblings
      mkSummary('ifc-bimspeed-warmond_ar--2-level', 'Project Name · 2. Level', {
        kind: 'ifc',
        credit: { text: 'BIM-Speed Warmond demo site', license: 'CC-BY-4.0', license_name: 'CC BY 4.0' },
      }),
      mkSummary('ifc-bimspeed-warmond_ar--ground-floor', 'Project Name · Ground floor', {
        kind: 'ifc',
        credit: { text: 'BIM-Speed Warmond demo site', license: 'CC-BY-4.0', license_name: 'CC BY 4.0' },
      }),
      // Multi-page PDF upload siblings (`up-<tok>` + `up-<tok>-p2`)
      mkSummary('up-abc999-p2', 'Villa Aurora — Page 2', { kind: 'raster', source_ext: '.pdf' }),
      mkSummary('up-abc999', 'Villa Aurora', { kind: 'raster', source_ext: '.pdf' }),
      // Batch-uploaded house stem (`Haus Sonnenberg`)
      mkSummary('up-hs3', 'Haus Sonnenberg · DG'),
      mkSummary('up-hs1', 'Haus Sonnenberg · KG'),
      mkSummary('up-hs2', 'Haus Sonnenberg · EG'),
    ]

    const groups = autoGroupDrawings(drawings)
    expect(groups).toHaveLength(3)

    const warmond = groups.find((g) => g.id === 'auto-ifc-bimspeed-warmond_ar')
    expect(warmond).toBeDefined()
    expect(warmond!.title).toBe('BIM-Speed Warmond')
    expect(warmond!.floors.map((f) => [f.drawing_id, f.label, f.order])).toEqual([
      ['ifc-bimspeed-warmond_ar--ground-floor', 'GF', 0],
      ['ifc-bimspeed-warmond_ar--2-level', 'Level 2', 2],
    ])

    const pdfProj = groups.find((g) => g.id === 'proj-abc999')
    expect(pdfProj).toBeDefined()
    expect(pdfProj!.title).toBe('Villa Aurora')
    expect(pdfProj!.floors.map((f) => [f.drawing_id, f.label, f.order])).toEqual([
      ['up-abc999', 'Page 1', 1],
      ['up-abc999-p2', 'Page 2', 2],
    ])

    const house = groups.find((g) => g.id === 'auto-stem-haus-sonnenberg')
    expect(house).toBeDefined()
    expect(house!.title).toBe('Haus Sonnenberg')
    expect(house!.floors.map((f) => [f.drawing_id, f.label, f.order])).toEqual([
      ['up-hs1', 'KG', -1],
      ['up-hs2', 'EG', 0],
      ['up-hs3', 'DG', 99],
    ])
  })

  it('persists manual bundles, sorts floors, resolves forDrawing and remembers ungrouped tombstones', () => {
    const d1 = mkSummary('swiss-4397', 'Grundriss EG', { subtitle: 'Site 403 · 1:200' })
    const d2 = mkSummary('swiss-46639', 'Plan 3e étage', { subtitle: 'Site 11420 · 1:200' })
    const created = myProjects.create('Custom Duplex', [d2, d1], 'proj-duplex')
    expect(created.auto_matched).toBe(false)
    expect(created.floors.map((f) => f.drawing_id)).toEqual(['swiss-4397', 'swiss-46639'])
    expect(myProjects.forDrawing('swiss-46639')?.id).toBe('proj-duplex')

    // No auto-grouping any more: same-named uploads stay separate, and a server auto-match is not adopted.
    const autoDrawings = [
      mkSummary('up-x1', 'Chalet Alpina · EG'),
      mkSummary('up-x2', 'Chalet Alpina · 1. OG'),
    ]
    expect(myProjects.resolveAll(autoDrawings).map((p) => p.id)).toEqual(['proj-duplex'])
    myProjects.syncFromServer([
      {
        id: 'auto-stem-chalet-alpina',
        title: 'Chalet Alpina',
        floors: [
          { drawing_id: 'up-x1', label: 'EG', order: 0 },
          { drawing_id: 'up-x2', label: '1. OG', order: 1 },
        ],
        auto_matched: true,
      },
    ])
    expect(myProjects.get('auto-stem-chalet-alpina')).toBeNull()
    // ...but the pages of one multi-page upload are still one project.
    myProjects.syncFromServer([
      {
        id: 'proj-up-pdf',
        title: 'house.pdf',
        floors: [
          { drawing_id: 'up-pdf', label: 'Page 1', order: 1 },
          { drawing_id: 'up-pdf-p2', label: 'Page 2', order: 2 },
        ],
        auto_matched: true,
      },
    ])
    expect(myProjects.get('proj-up-pdf')?.floors).toHaveLength(2)
  })

  it('does not poison floor order/labels or project titles with random up-<id> tokens like up-2f or up-b1', () => {
    const drawings: DrawingSummary[] = [
      mkSummary('up-2f', 'Villa Nord'),
      mkSummary('up-b1', 'Villa Nord'),
      mkSummary('up-raw99', 'up-raw99', { kind: 'raster', source_ext: '.pdf' }),
      mkSummary('up-raw99-p2', 'up-raw99', { kind: 'raster', source_ext: '.pdf' }),
    ]
    const groups = autoGroupDrawings(drawings)
    const villa = groups.find((g) => g.id === 'auto-stem-villa-nord')
    expect(villa).toBeDefined()
    expect(villa!.floors.map((f) => [f.drawing_id, f.label, f.order])).toEqual([
      ['up-2f', 'Page 1', 1],
      ['up-b1', 'Page 2', 2],
    ])
    const rawPdf = groups.find((g) => g.id === 'proj-raw99')
    expect(rawPdf).toBeDefined()
    expect(rawPdf!.title).toBe('up-raw99')
    expect(rawPdf!.floors.map((f) => [f.drawing_id, f.label, f.order])).toEqual([
      ['up-raw99', 'Page 1', 1],
      ['up-raw99-p2', 'Page 2', 2],
    ])
  })
})

describe('ungrouping a building the server derives from files', () => {
  it('stays ungrouped across syncs and resolves, and grouping it again works', async () => {
    const { myProjects } = await import('./library')
    myProjects.clear()
    const ifc = {
      id: 'proj-ifc-m',
      title: 'Model',
      auto_matched: false,
      floors: [
        { drawing_id: 'ifc-m--a', label: 'A', order: 0 },
        { drawing_id: 'ifc-m--b', label: 'B', order: 1 },
      ],
    }
    myProjects.syncFromServer([ifc])
    expect(myProjects.resolveAll([], [ifc]).map((p) => p.id)).toEqual(['proj-ifc-m'])
    myProjects.remove('proj-ifc-m')
    myProjects.syncFromServer([ifc])
    expect(myProjects.list().map((p) => p.id)).not.toContain('proj-ifc-m')
    expect(myProjects.resolveAll([], [ifc])).toEqual([])
    myProjects.save(ifc)
    expect(myProjects.resolveAll([], [ifc]).map((p) => p.id)).toEqual(['proj-ifc-m'])
    myProjects.clear()
  })
})

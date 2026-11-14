import { describe, expect, it } from 'vitest'
import { bundledDrawing } from '@/data/drawing'
import { BUNDLED_SUMMARY } from '@/data/ids'
import { apiUrl, checkFile, compactCredit, displayDrawingTitle, factsRatio, kindText } from './library'

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

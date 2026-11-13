import { describe, expect, it } from 'vitest'
import { bundledDrawing } from '@/data/drawing'
import { BUNDLED_SUMMARY } from '@/data/ids'
import { apiUrl, checkFile } from './library'

const MB = 1024 * 1024

describe('upload checks mirror the server caps', () => {
  it('accepts DXF up to 20 MB for everyone', () => {
    expect(checkFile({ name: 'A.DXF', size: 20 * MB }, false)).toEqual({ ok: true, type: 'dxf' })
    expect(checkFile({ name: 'a.dxf', size: 20 * MB + 1 }, false)).toMatchObject({ ok: false, code: 'too_large' })
    expect(checkFile({ name: 'a.dxf', size: 0 }, false)).toMatchObject({ ok: false, code: 'empty' })
  })
  it('PNG/JPG (15 MB) and PDF (20 MB) need presenter access', () => {
    expect(checkFile({ name: 'a.png', size: 10 }, false)).toMatchObject({ ok: false, code: 'presenter_required' })
    expect(checkFile({ name: 'a.jpeg', size: 15 * MB }, true)).toEqual({ ok: true, type: 'image' })
    expect(checkFile({ name: 'a.jpg', size: 15 * MB + 1 }, true)).toMatchObject({ ok: false, code: 'too_large' })
    expect(checkFile({ name: 'a.pdf', size: 20 * MB }, true)).toEqual({ ok: true, type: 'pdf' })
  })
  it('refuses DWG, zip and unknown types', () => {
    for (const name of ['a.dwg', 'a.zip', 'a', 'a.dxf.exe'])
      expect(checkFile({ name, size: 10 }, true)).toMatchObject({ ok: false, code: 'unsupported_type' })
  })
})

describe('apiUrl', () => {
  it('resolves root-relative URLs against the API base only', () => {
    expect(apiUrl('/drawings/x/plan.svg', 'https://api.example/')).toBe('https://api.example/drawings/x/plan.svg')
    expect(apiUrl('https://cdn/x.png', 'https://api.example')).toBe('https://cdn/x.png')
    expect(apiUrl('//cdn/x.png', 'https://api.example')).toBe('//cdn/x.png')
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

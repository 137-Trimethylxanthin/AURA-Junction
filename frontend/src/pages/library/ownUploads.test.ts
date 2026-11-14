import { describe, expect, it } from 'vitest'
import type { DrawingStatusBody } from '@/api/contract'
import type { UploadRow } from '@/pages/upload/useUploads'
import fixture from '@/pages/upload/status-contract.fixture.json'
import { LIBRARY_SHOWS_BUNDLED, fileTypeOf, inProgressCount, ownUploadEntries } from './ownUploads'

const row = (id: string, name: string, phase: UploadRow['phase'], status?: DrawingStatusBody, at = '2026-11-14T10:00:00Z'): UploadRow => ({
  id,
  name,
  delete_token: 'tok',
  created_at: at,
  phase,
  status,
})

describe('own uploads in the library', () => {
  it('only finished uploads become cards; the rest count as in progress', () => {
    const rows = [
      row('up-a', 'a.dxf', 'ready', fixture.variants.dxf_upload_full_mode as DrawingStatusBody),
      row('up-b', 'b.pdf', 'processing'),
      row('up-c', 'c.png', 'failed'),
      row('local-1', 'd.pdf', 'uploading'),
    ]
    expect(ownUploadEntries(rows, 'full', (n) => `Page ${n}`).map((o) => o.summary.id)).toEqual(['up-a'])
    expect(inProgressCount(rows)).toBe(2)
  })

  it('a multi-page PDF gives one card per page; only its own card offers Delete; a page still reading keeps a stage', () => {
    const body = fixture.sequence_multipage_pdf_mid_way as DrawingStatusBody
    const entries = ownUploadEntries([row(body.id, 'house.pdf', 'ready', body)], 'full', (n) => `Page ${n}`)
    expect(entries.map((o) => o.summary.id)).toEqual(['up-H7a', 'up-H7a-p2', 'up-H7a-p3'])
    expect(entries.map((o) => o.deletable)).toEqual([true, false, false])
    expect(entries[0].summary.subtitle).toBe('Page 1 · EG')
    expect(entries[2].view.outcome).toBe('working')
    expect(entries[0].view.outcome).toBe('done')
    expect(entries[0].summary.thumbnail).toBe('/drawings/up-H7a/thumb.png')
  })

  it('newest first, with the file type from the extension', () => {
    const rows = [
      row('up-old', 'plan.JPEG', 'ready', undefined, '2026-11-14T08:00:00Z'),
      row('up-new', 'model.ifc', 'ready', undefined, '2026-11-14T09:00:00Z'),
    ]
    const entries = ownUploadEntries(rows, null, (n) => `Page ${n}`)
    expect(entries.map((o) => o.summary.id)).toEqual(['up-new', 'up-old'])
    expect(entries.map((o) => fileTypeOf(o.summary))).toEqual(['IFC', 'JPG'])
    expect(fileTypeOf({ title: 'Swiss plan', kind: 'cad_dxf' })).toBe('DXF')
    expect(fileTypeOf({ title: 'x', kind: 'raster', source_ext: '.pdf' })).toBe('PDF')
    expect(fileTypeOf({ title: 'drawing.svg', kind: 'raster' })).toBe('SVG')
  })

  it('bundled drawings are listed unless the build turns them off', () => {
    expect(LIBRARY_SHOWS_BUNDLED).toBe(true) // tests and the hosted build; dev sets VITE_LIBRARY_BUNDLED=0
  })
})

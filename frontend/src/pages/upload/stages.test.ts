import { describe, expect, it } from 'vitest'
import type { DrawingStatusBody } from '@/api/contract'
import { isDrawingStatusBody } from '@/api/contract'
import fixture from './status-contract.fixture.json'
import { estimatedCounts, isLive, realFraction, reportedCounts, stageView, STEPS, TOO_SLOW_MS } from './stages'

const seq = (fixture.sequence_pdf_full_mode_recorded_shape as { body: DrawingStatusBody }[]).map((s) => s.body)
const variants = fixture.variants as Record<string, DrawingStatusBody>
const viewOf = (b: DrawingStatusBody, opts: Parameters<typeof stageView>[2] = {}) => stageView(b.status, b.stage, { mode: b.mode, ...opts })

describe('status contract fixtures', () => {
  it('every recorded body still passes the contract check', () => {
    for (const b of [...seq, fixture.sequence_multipage_pdf_mid_way as DrawingStatusBody, ...Object.values(variants)])
      expect(isDrawingStatusBody(b), b.id).toBe(true)
  })
})

describe('stageView along the recorded PDF sequence', () => {
  it('moves forward one step at a time, opens from `reading` on, and stops polling only at the end', () => {
    const views = seq.map((b) => viewOf(b))
    expect(views.map((v) => v.step)).toEqual(['queued', 'parsing', 'reading', 'interpreting', 'materialising', 'indexed'])
    expect(views.map((v) => v.openable)).toEqual([false, false, true, true, true, true])
    expect(views.map(isLive)).toEqual([true, true, true, true, true, false])
    expect(views.at(-1)!.outcome).toBe('done')
    for (let i = 1; i < views.length; i++) expect(views[i].index).toBeGreaterThan(views[i - 1].index)
  })

  it('a real fraction only where the server measures one', () => {
    expect(seq.map((b) => realFraction(b.progress))).toEqual([null, 0.4, null, null, null, null])
    expect(realFraction(1.4)).toBeNull()
    expect(realFraction(Number.NaN)).toBeNull()
  })

  it('marks the symbol-detected counts as estimates once the store spent model calls, never CAD counts', () => {
    expect([...estimatedCounts(seq[2])]).toEqual([]) // reading: 0 calls
    expect([...estimatedCounts(seq.at(-1))].sort()).toEqual(['doors', 'fixtures', 'windows'])
    expect([...estimatedCounts(variants.dxf_upload_full_mode)]).toEqual([])
    // `methods` from the backend decide when present: only "vision" is an estimate, per page when given.
    const withMethods = { ...seq.at(-1)!, methods: { rooms: 'pdf_vector', doors: 'vision', windows: 'ocr' } }
    expect([...estimatedCounts(withMethods)]).toEqual(['doors'])
    expect([...estimatedCounts(withMethods, { rooms: 'cad_geometry', windows: 'vision' })]).toEqual(['windows'])
    expect(reportedCounts(seq.at(-1)!.counts).map((c) => c.key)).toEqual(['rooms', 'apartments', 'doors', 'windows', 'fixtures', 'texts'])
  })
})

describe('final states', () => {
  it('DXF/IFC handled by our solvers is done, exact', () => {
    expect(viewOf(variants.dxf_upload_full_mode)).toMatchObject({ step: 'indexed', outcome: 'done', openable: true })
  })
  it('quick mode is ready but says it is not indexed', () => {
    expect(viewOf(variants.quick_mode_pdf)).toMatchObject({ outcome: 'quick', openable: true })
  })
  it('store_failed stays openable and is drawn where the store stopped', () => {
    expect(viewOf(variants.store_failed, { last: 'interpreting' })).toMatchObject({ step: 'interpreting', outcome: 'store_failed', openable: true })
    expect(viewOf(variants.store_failed)).toMatchObject({ step: 'reading', outcome: 'store_failed' })
  })
  it('partial and failed', () => {
    expect(viewOf(variants.partial)).toMatchObject({ outcome: 'partial', openable: true })
    expect(viewOf(variants.failed, { last: 'parsing' })).toMatchObject({ step: 'parsing', outcome: 'failed', openable: false })
  })
  it('too slow only in store stages', () => {
    expect(viewOf(seq[3], { slow: true }).outcome).toBe('too_slow')
    expect(viewOf(seq[1], { slow: true }).outcome).toBe('working')
    expect(TOO_SLOW_MS).toBe(12 * 60_000)
  })
})

describe('an older backend without stages', () => {
  it('still shows the four statuses', () => {
    expect(stageView('queued', undefined)).toMatchObject({ step: 'queued', outcome: 'working' })
    expect(stageView('processing', undefined)).toMatchObject({ step: 'parsing', outcome: 'working' })
    expect(stageView('ready', undefined)).toMatchObject({ step: 'indexed', outcome: 'done', openable: true })
    expect(stageView('failed', undefined).outcome).toBe('failed')
    expect(stageView('uploading', undefined).step).toBe('upload')
    expect(STEPS[0]).toBe('upload')
  })
  it('an unknown future stage keeps working at the last known step', () => {
    expect(stageView('ready', 'vectorising', { last: 'reading' })).toMatchObject({ step: 'reading', outcome: 'working' })
  })
})

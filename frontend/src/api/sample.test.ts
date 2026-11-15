import { describe, expect, it } from 'vitest'
import { BUNDLED_DRAWING_ID, BUNDLED_SUMMARY } from '@/data/ids'
import { sampleDrawingId } from './sample'

const server = (drawings: unknown[] | null) => ({
  baseUrl: 'http://api.test',
  fetch: (async () =>
    drawings === null
      ? new Response('nope', { status: 500 })
      : new Response(JSON.stringify({ contract_version: '1.2', drawings }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })) as typeof globalThis.fetch,
})
const other = (id: string, status = 'ready') => ({ ...BUNDLED_SUMMARY, id, status })

describe('the sample drawing is one the server has', () => {
  it('is the bundled drawing without a backend', async () => {
    expect(await sampleDrawingId(null)).toBe(BUNDLED_DRAWING_ID)
  })
  it('is the bundled drawing when the server lists it', async () => {
    expect(await sampleDrawingId(server([other('swiss-1'), BUNDLED_SUMMARY]))).toBe(BUNDLED_DRAWING_ID)
  })
  it('is the first ready drawing of the server when it does not have the bundled one', async () => {
    expect(await sampleDrawingId(server([other('up-x', 'processing'), other('swiss-2233'), other('swiss-9')]))).toBe(
      'swiss-2233',
    )
  })
  it('falls back to the bundled drawing when the list is empty or unreachable', async () => {
    expect(await sampleDrawingId(server([]))).toBe(BUNDLED_DRAWING_ID)
    expect(await sampleDrawingId(server(null))).toBe(BUNDLED_DRAWING_ID)
  })
})

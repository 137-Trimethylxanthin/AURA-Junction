import { describe, expect, it } from 'vitest'
import { traceDetail } from './detail'

describe('traceDetail', () => {
  it('reads a decision node', () => {
    const d = traceDetail({
      stage: 'decision',
      detail: {
        decider: 'gemini-decision-1',
        chosen: 'doors',
        confidence: 0.87,
        route: 'exact',
        matched: ['doors', 7],
        runner_up: 'windows',
        runner_up_confidence: 0.1,
      },
    }).decision
    expect(d).toMatchObject({ decider: 'gemini-decision-1', confidencePct: 87, route: 'exact', matched: ['doors'], runnerUp: 'windows', runnerUpPct: 10 })
  })
  it('reads resources, filter and answer nodes', () => {
    expect(traceDetail({ stage: 'resources', detail: { tables: ['rooms', 'doors'], grabbed: 12, ms: 3 } }).resources).toEqual({ tables: 'rooms, doors', grabbed: 12, ms: 3 })
    const f = traceDetail({ stage: 'filter', detail: { rule: 'on floor', kept: ['a', 'b'], dropped: [{ id: 'c', reason: 'other floor' }, 5] } }).filter
    expect(f).toEqual({ rule: 'on floor', kept: 2, keptIds: ['a', 'b'], dropped: 2, reasons: [{ id: 'c', reason: 'other floor' }] })
    expect(traceDetail({ stage: 'answer', detail: { source: 'exact', calls: 1, cost_usd: 0.002 } }).answer).toEqual({ source: 'exact', calls: 1, cost: 0.002 })
  })
  it('tolerates missing or malformed detail', () => {
    expect(traceDetail({})).toEqual({ stage: undefined })
    expect(traceDetail({ stage: 'filter', detail: { kept: 'x', dropped: 3 } }).filter).toMatchObject({ kept: undefined, dropped: undefined, reasons: [] })
  })
  it('reads a query on any stage and the kept ids of a filter', () => {
    const r = traceDetail({
      stage: 'resources',
      detail: { query: 'SELECT id, m2 FROM rooms', rows: 7, columns: ['id', 'm2', 5], ms: 4 },
    })
    expect(r.sql).toEqual({ query: 'SELECT id, m2 FROM rooms', rows: 7, columns: ['id', 'm2'], ms: 4 })
    const f = traceDetail({ stage: 'filter', detail: { rule: 'sum of column m2 over 7 rows', kept: ['r1', 2, {}] } }).filter
    expect(f).toMatchObject({ rule: 'sum of column m2 over 7 rows', kept: 3, keptIds: ['r1', '2'] })
    expect(traceDetail({ stage: 'resources', detail: { query: '  ' } }).sql).toBeUndefined()
  })
})

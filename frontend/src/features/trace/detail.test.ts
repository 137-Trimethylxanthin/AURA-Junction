import { describe, expect, it } from 'vitest'
import type { TraceNode } from '@/api/contract'
import { traceDetail } from './detail'

const node = (args: Record<string, unknown>) =>
  ({ tool: { name: 'count_objects', args, result_id: 'r', ms: 1 } }) as Pick<TraceNode, 'tool'>

describe('traceDetail', () => {
  it('reads decision, kept/dropped and grabbed fields', () => {
    const d = traceDetail(
      node({
        decision: { question: 'q', chosen: 'doors', confidence: 0.87, matched: 'doors', runner_up: 'windows' },
        kept_ids: ['a', 'b'],
        dropped_ids: ['c'],
        grabbed: { source: 'ifc', count: 12, ms: 3 },
      }),
    )
    expect(d.decision).toMatchObject({ confidencePct: 87, matched: 'doors', runnerUp: 'windows' })
    expect([d.kept, d.dropped]).toEqual([2, 1])
    expect(d.grabbed).toEqual({ source: 'ifc', count: 12, ms: 3 })
  })
  it('ignores missing or malformed fields', () => {
    expect(traceDetail({})).toEqual({})
    expect(traceDetail(node({ decision: 'x', kept_ids: 'y', grabbed: 3 }))).toEqual({})
  })
})

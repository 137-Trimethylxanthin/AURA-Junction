import type { TraceNode } from '@/api/contract'

export type Decider = 'router' | 'context' | 'gemini-decision-1'

export interface TraceDetail {
  stage?: string
  decision?: {
    decider?: Decider | string
    question?: string
    chosen?: string
    /** 0–100 */
    confidencePct?: number
    route?: string
    matched: string[]
    runnerUp?: string
    runnerUpPct?: number
  }
  resources?: { tables?: string; grabbed?: number; ms?: number }
  filter?: { rule?: string; kept?: number; dropped?: number; reasons: { id: string; reason?: string }[] }
  answer?: { source?: string; calls?: number; cost?: number }
}

const str = (v: unknown): string | undefined => (typeof v === 'string' && v.trim() ? v.trim() : undefined)
const num = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined)
const rec = (v: unknown): Record<string, unknown> | undefined =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : undefined
const pct = (v: unknown): number | undefined => {
  const n = num(v)
  return n === undefined ? undefined : Math.round(Math.min(100, Math.max(0, n <= 1 ? n * 100 : n)))
}
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.map(str).filter((x): x is string => !!x) : [])
/** A table list may arrive as an array or a string. */
const list = (v: unknown): string | undefined => (Array.isArray(v) ? strs(v).join(', ') || undefined : str(v))

/** Stage detail of a trace node (`stage` + `detail`); missing or malformed fields are skipped, never thrown on. */
export function traceDetail(node: Pick<TraceNode, 'stage' | 'detail'>): TraceDetail {
  const d = rec(node.detail)
  const out: TraceDetail = { stage: str(node.stage) }
  if (!d) return out
  if (node.stage === 'decision') {
    out.decision = {
      decider: str(d.decider),
      question: str(d.question),
      chosen: str(d.chosen),
      confidencePct: pct(d.confidence),
      route: str(d.route),
      matched: strs(d.matched),
      runnerUp: str(d.runner_up),
      runnerUpPct: pct(d.runner_up_confidence),
    }
  } else if (node.stage === 'resources') {
    out.resources = { tables: list(d.tables), grabbed: num(d.grabbed), ms: num(d.ms) }
  } else if (node.stage === 'filter') {
    const dropped = Array.isArray(d.dropped) ? d.dropped : undefined
    out.filter = {
      rule: str(d.rule),
      kept: Array.isArray(d.kept) ? d.kept.length : undefined,
      dropped: dropped?.length,
      reasons: (dropped ?? []).flatMap((x) => {
        const r = rec(x)
        const id = str(r?.id)
        return id ? [{ id, reason: str(r?.reason) }] : []
      }),
    }
  } else if (node.stage === 'answer') {
    out.answer = { source: str(d.source), calls: num(d.calls), cost: num(d.cost_usd) }
  }
  return out
}

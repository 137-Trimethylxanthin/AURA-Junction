import type { TraceNode } from '@/api/contract'

export interface TraceDecision {
  question?: string
  chosen?: string
  /** 0–100 */
  confidencePct?: number
  matched?: string
  runnerUp?: string
}

export interface TraceDetail {
  decision?: TraceDecision
  kept?: number
  dropped?: number
  grabbed?: { source?: string; count?: number; ms?: number }
}

const str = (v: unknown): string | undefined => (typeof v === 'string' && v.trim() ? v.trim() : undefined)
const num = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined)
const rec = (v: unknown): Record<string, unknown> | undefined =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : undefined

/** Optional tool-step fields (`tool.args.decision|kept_ids|dropped_ids|grabbed`); anything missing or malformed is skipped. */
export function traceDetail(node: Pick<TraceNode, 'tool'>): TraceDetail {
  const args = rec(node.tool?.args)
  if (!args) return {}
  const out: TraceDetail = {}
  const d = rec(args.decision)
  if (d) {
    const c = num(d.confidence)
    out.decision = {
      question: str(d.question),
      chosen: str(d.chosen),
      confidencePct: c === undefined ? undefined : Math.round(Math.min(100, Math.max(0, c <= 1 ? c * 100 : c))),
      matched: str(d.matched),
      runnerUp: str(d.runner_up),
    }
  }
  if (Array.isArray(args.kept_ids)) out.kept = args.kept_ids.length
  if (Array.isArray(args.dropped_ids)) out.dropped = args.dropped_ids.length
  const g = rec(args.grabbed)
  if (g) out.grabbed = { source: str(g.source), count: num(g.count), ms: num(g.ms) }
  return out
}

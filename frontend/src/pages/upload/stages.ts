import type { DrawingStatus, DrawingStatusBody, UploadCounts, UploadMethods } from '@/api/contract'
import type { UiKey } from '@/i18n/strings'

/**
 * The upload pipeline as the page draws it. `status` (queued | processing | ready | failed) is what every backend
 * sends; `stage` is the optional fine-grained store stage. Without a stage the page still shows the old statuses.
 */
export const STEPS = ['upload', 'queued', 'parsing', 'reading', 'interpreting', 'materialising', 'indexed'] as const
export type Step = (typeof STEPS)[number]

export const STEP_LABEL: Record<Step, UiKey> = {
  upload: 'ul_st_upload',
  queued: 'ul_st_queued',
  parsing: 'ul_st_rendering',
  reading: 'ul_st_reading',
  interpreting: 'ul_st_interpreting',
  materialising: 'ul_st_materialising',
  indexed: 'ul_st_indexed',
}

/** Stages that run in the drawing store; time spent here counts towards "too slow". */
export const STORE_STEPS: ReadonlySet<Step> = new Set(['reading', 'interpreting', 'materialising'])
/** Over this long in store stages, the page stops waiting and says so. */
export const TOO_SLOW_MS = 12 * 60_000

export type Outcome = 'working' | 'done' | 'quick' | 'partial' | 'store_failed' | 'failed' | 'too_slow' | 'expired'

export interface StageView {
  step: Step
  index: number
  outcome: Outcome
  /** The drawing can be opened (our fast parse finished). */
  openable: boolean
}

export type Phase = 'uploading' | DrawingStatus | 'expired'

const at = (step: Step, outcome: Outcome, openable: boolean): StageView => ({
  step,
  index: STEPS.indexOf(step),
  outcome,
  openable,
})

/**
 * Where a file stands. `last` is the last step seen (a failure is drawn there); `mode` comes from the status or
 * /health; `slow` is set by the poller after TOO_SLOW_MS in store stages.
 */
export function stageView(
  phase: Phase,
  stage: string | undefined,
  opts: { mode?: 'full' | 'quick'; last?: Step; slow?: boolean } = {},
): StageView {
  const openable = phase === 'ready'
  const last = opts.last ?? (openable ? 'indexed' : 'parsing')
  if (phase === 'expired') return at(last, 'expired', false)
  if (phase === 'uploading') return at('upload', 'working', false)
  if (phase === 'failed' || stage === 'failed') return at(last, 'failed', false)
  switch (stage) {
    case undefined:
    case '':
      if (phase === 'queued') return at('queued', 'working', false)
      if (phase === 'processing') return at('parsing', 'working', false)
      return at('indexed', 'done', true)
    case 'indexed':
    case 'indexed_store':
    case 'indexed_ours': // DXF/IFC: our solvers own rooms and openings; final
      return at('indexed', 'done', openable)
    case 'rendering':
      return at('parsing', 'working', openable)
    case 'ready_unindexed':
      return at('indexed', 'quick', openable)
    case 'partial':
      return at(opts.last && STORE_STEPS.has(opts.last) ? opts.last : 'interpreting', 'partial', openable)
    case 'store_failed':
      return at(opts.last && STORE_STEPS.has(opts.last) ? opts.last : 'reading', 'store_failed', openable)
    default: {
      const known = (STEPS as readonly string[]).includes(stage) ? (stage as Step) : last
      const slow = opts.slow && STORE_STEPS.has(known)
      return at(known, slow ? 'too_slow' : 'working', openable)
    }
  }
}

/** Still worth polling. */
export const isLive = (v: StageView) => v.outcome === 'working'

export const COUNT_KEYS = ['rooms', 'apartments', 'doors', 'windows', 'fixtures', 'texts'] as const
/** Subjects the store finds by symbol detection (a model) when it spent ingest calls: shown as estimates. */
export const DETECTED: ReadonlySet<CountKey> = new Set(['doors', 'windows', 'fixtures'])
export type CountKey = (typeof COUNT_KEYS)[number]

/** Only the counts the backend reported, in a fixed order. */
export function reportedCounts(c: UploadCounts | undefined): { key: CountKey; n: number }[] {
  if (!c) return []
  return COUNT_KEYS.flatMap((key) => {
    const n = c[key]
    return typeof n === 'number' && Number.isFinite(n) && n >= 0 ? [{ key, n }] : []
  })
}

/** A fraction to draw, or null for an indeterminate bar. Never invented: only a finite 0-1 number from the server. */
export function realFraction(p: unknown): number | null {
  return typeof p === 'number' && Number.isFinite(p) && p >= 0 && p <= 1 ? p : null
}

/** "3 min 05 s" style elapsed time. */
export function elapsed(ms: number): { m: number; s: number } {
  const total = Math.max(0, Math.floor(ms / 1000))
  return { m: Math.floor(total / 60), s: total % 60 }
}

/**
 * Counts to mark "(estimate)". The backend's `methods` decide ("vision" = a model estimate); `pageMethods` override
 * the file's for one page. Only an older body without `methods` falls back to: the store's symbol-detected
 * subjects when it spent model calls. CAD/IFC (indexed_ours) counts are exact.
 */
export function estimatedCounts(st: DrawingStatusBody | undefined, pageMethods?: UploadMethods): Set<string> {
  const methods = pageMethods ?? st?.methods
  if (methods) return new Set(Object.entries(methods).filter(([, m]) => m === 'vision').map(([k]) => k))
  if (st?.estimates) return new Set(st.estimates)
  if (!st || st.stage === 'indexed_ours' || !(st.ingest_calls && st.ingest_calls > 0)) return new Set()
  return new Set(DETECTED)
}

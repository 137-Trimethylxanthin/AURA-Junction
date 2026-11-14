import { useUi } from '@/i18n/uiLang'
import { cn } from '@/lib/utils'
import { STEPS, STEP_LABEL, STORE_STEPS, type StageView } from './stages'

/**
 * The pipeline as one segmented rail: finished steps solid, the current one live, later ones faint. A real
 * fraction fills the current segment; without one it carries a calm indeterminate sweep (static under reduced
 * motion). Steps the server skips in quick mode are drawn dashed. One line of text says where the file is.
 */
export function StageTimeline({
  view,
  fraction,
  quickSkips = false,
  dense = false,
  stageSeconds,
  className,
}: {
  view: StageView
  /** 0-1 from the server or the browser; null draws the indeterminate sweep. */
  fraction: number | null
  /** Quick mode: the store steps never run. */
  quickSkips?: boolean
  dense?: boolean
  /** Seconds in the current step, from the server; shown beside an indeterminate bar. */
  stageSeconds?: number
  className?: string
}) {
  const { tu } = useUi()
  const { index, outcome } = view
  const live = outcome === 'working'
  const pct = fraction === null ? null : Math.round(fraction * 100)
  const tone =
    outcome === 'failed' || outcome === 'store_failed'
      ? 'bg-destructive'
      : outcome === 'partial' || outcome === 'too_slow'
        ? 'bg-warning'
        : 'bg-foreground'

  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', className)} data-stage={view.step} data-outcome={outcome}>
      <ol
        className={cn('flex w-full gap-1', dense ? 'h-1' : 'h-1.5')}
        aria-label={tu('ul_pipeline')}
      >
        {STEPS.map((s, i) => {
          const skipped = quickSkips && STORE_STEPS.has(s)
          const done = outcome === 'done' || outcome === 'quick' ? !skipped : i < index
          const current = i === index && outcome !== 'done' && outcome !== 'quick'
          const state = skipped ? 'skipped' : done ? 'done' : current ? 'current' : 'todo'
          return (
            <li
              key={s}
              data-step={s}
              data-state={state}
              className={cn(
                'relative min-w-0 flex-1 overflow-hidden rounded-full',
                state === 'skipped' ? 'border border-dashed border-border bg-transparent' : 'bg-muted',
              )}
            >
              <span className="sr-only">
                {tu(STEP_LABEL[s])}: {tu(`ul_state_${state}` as const)}
              </span>
              {state === 'done' ? <span aria-hidden className="absolute inset-0 rounded-full bg-foreground" /> : null}
              {state === 'current' ? (
                pct !== null && live ? (
                  <span
                    aria-hidden
                    className={cn('absolute inset-y-0 left-0 rounded-full transition-[width] duration-300 ease-out', tone)}
                    style={{ width: `${Math.max(6, pct)}%` }}
                  />
                ) : live ? (
                  <span aria-hidden className="ul-sweep absolute inset-0 rounded-full" />
                ) : (
                  <span aria-hidden className={cn('absolute inset-0 rounded-full', tone)} />
                )
              ) : null}
            </li>
          )
        })}
      </ol>
      {dense ? null : (
        <p className="flex min-w-0 items-baseline gap-1.5 text-xs text-muted-foreground" aria-live="polite">
          <span className="truncate font-medium text-foreground">
            {outcome === 'done'
              ? tu('ul_st_indexed')
              : outcome === 'quick'
                ? tu('ul_quick_ready')
                : tu(STEP_LABEL[view.step])}
          </span>
          {live ? (
            <span className="num shrink-0">
              {tu('ul_step_of', { n: index + 1, total: STEPS.length })}
              {pct !== null
                ? ` · ${pct} %`
                : stageSeconds !== undefined && stageSeconds > 0
                  ? ` · ${tu('ul_in_step', { m: Math.floor(stageSeconds / 60), s: String(stageSeconds % 60).padStart(2, '0') })}`
                  : ''}
            </span>
          ) : null}
        </p>
      )}
    </div>
  )
}

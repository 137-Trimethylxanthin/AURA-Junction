import { motion, useReducedMotion } from 'motion/react'
import { CheckIcon, GitBranchIcon, ScanSearchIcon, SquareIcon } from 'lucide-react'
import type { Lang } from '@/api/contract'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { Trace, TraceDescription, TraceIcon, TraceLine, TraceTitle } from '@/components/ui/trace'
import { t } from '@/i18n/strings'
import { fmtCount } from '@/lib/format'
import { actions, type AgentTurn } from '@/store'

/**
 * The visible "working" state of a streamed answer: one line per step as the /chat events arrive (understood,
 * each tool call with its live status, the Gemini check), plus Stop. Steps rise in with a critically damped
 * spring; with reduced motion they fade.
 */
export function StreamSteps({ turn, lang }: { turn: AgentTurn; lang: Lang }) {
  const reduce = useReducedMotion()
  const st = turn.stream
  const resolved = new Set(st?.resolved ?? [])
  const enter = {
    initial: reduce ? { opacity: 0 } : { opacity: 0, y: 6 },
    animate: { opacity: 1, y: 0 },
    transition: reduce ? { duration: 0.12 } : ({ type: 'spring', bounce: 0, duration: 0.3 } as const),
  }
  const done = <CheckIcon className="text-foreground" />
  return (
    <div className="flex flex-col gap-3" data-stream-steps={st?.phase ?? 'sent'}>
      <Trace aria-live="polite" aria-label="Progress">
        <motion.div {...enter}>
          <TraceLine status={st?.understood ? undefined : 'running'}>
            <TraceIcon>{st?.understood ? done : <Spinner />}</TraceIcon>
            <TraceTitle>{t(lang, st?.understood ? 'stream_understood' : 'stream_reading')}</TraceTitle>
            {st?.understood ? (
              <TraceDescription className="min-w-0 truncate" lang="en" title={st.understood.canonical_en}>
                “{st.understood.canonical_en}”
              </TraceDescription>
            ) : null}
          </TraceLine>
        </motion.div>
        {st?.nodes.map((n) => {
          const ok = resolved.has(n.id)
          return (
            <motion.div key={n.id} {...enter} data-stream-node={n.id} data-state={ok ? 'done' : 'running'}>
              <TraceLine status={ok ? undefined : 'running'}>
                <TraceIcon>{ok ? done : <Spinner />}</TraceIcon>
                <TraceTitle>{n.title}</TraceTitle>
                {n.tool ? <TraceDescription className="num">{n.tool.name}</TraceDescription> : null}
                {ok && n.evidence_ids?.length ? (
                  <TraceDescription>· {t(lang, 'stream_on_plan', { n: fmtCount(n.evidence_ids.length, lang) })}</TraceDescription>
                ) : null}
              </TraceLine>
            </motion.div>
          )
        })}
        {st?.check ? (
          <motion.div {...enter} data-stream-check>
            <TraceLine status="running">
              <TraceIcon>
                <ScanSearchIcon />
              </TraceIcon>
              <TraceTitle>{t(lang, 'stream_checked', { n: fmtCount(st.check.ranking.length, lang) })}</TraceTitle>
            </TraceLine>
          </motion.div>
        ) : null}
      </Trace>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" onClick={() => actions().cancel()} data-stop>
          <SquareIcon data-icon="inline-start" />
          {t(lang, 'stop')}
        </Button>
        {st?.nodes.length ? (
          <Button
            variant="ghost"
            onClick={() => {
              actions().setActive(turn.id)
              actions().setTraceOpen(true)
            }}
          >
            <GitBranchIcon data-icon="inline-start" />
            {t(lang, 'reasoning_tree')}
          </Button>
        ) : null}
      </div>
    </div>
  )
}

import { motion, useReducedMotion } from 'motion/react'
import { CheckIcon, GitBranchIcon, ScanSearchIcon, SquareIcon } from 'lucide-react'
import type { Lang } from '@/api/contract'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { Trace, TraceDescription, TraceIcon, TraceLine, TraceTitle } from '@/components/ui/trace'
import { t } from '@/i18n/strings'
import { tu } from '@/i18n/uiLang'
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
      <Trace aria-live="polite" aria-label={tu('steps_progress')}>
        {st?.queuePosition !== undefined && st.queuePosition > 0 ? (
          <motion.div {...enter} data-stream-queue={st.queuePosition}>
            <TraceLine status="running" className="overflow-hidden">
              <TraceIcon>
                <Spinner />
              </TraceIcon>
              <TraceTitle className="num truncate">
                {t(lang, 'stream_queue', { n: fmtCount(st.queuePosition, lang) })}
              </TraceTitle>
            </TraceLine>
          </motion.div>
        ) : null}
        <motion.div {...enter}>
          <TraceLine status={st?.understood ? undefined : 'running'} className="overflow-hidden">
            <TraceIcon>{st?.understood ? done : <Spinner />}</TraceIcon>
            <TraceTitle className="shrink-0">{t(lang, st?.understood ? 'stream_understood' : 'stream_reading')}</TraceTitle>
            {st?.understood ? (
              <TraceDescription className="min-w-0 shrink truncate" lang="en" title={st.understood.canonical_en}>
                “{st.understood.canonical_en}”
              </TraceDescription>
            ) : null}
          </TraceLine>
        </motion.div>
        {st?.nodes.map((n, idx) => {
          const ok = resolved.has(n.id)
          const isAgent = n.tool?.name === 'deep_agent'
          const rawSeq = n.tool?.args?.seq
          const seq = typeof rawSeq === 'number' && Number.isFinite(rawSeq) && rawSeq > 0 ? rawSeq : idx + 1
          const title = isAgent
            ? n.title.trim() || t(lang, 'stream_agent_step', { n: fmtCount(seq, lang) })
            : n.title
          const stepArg = typeof n.tool?.args?.step === 'string' ? n.tool.args.step.trim() : ''
          const detail = isAgent
            ? stepArg && stepArg !== title
              ? stepArg
              : t(lang, 'stream_agent_step', { n: fmtCount(seq, lang) }) !== title
                ? t(lang, 'stream_agent_step', { n: fmtCount(seq, lang) })
                : 'deep_agent'
            : n.tool?.name
          return (
            <motion.div key={n.id} {...enter} data-stream-node={n.id} data-state={ok ? 'done' : 'running'}>
              <TraceLine status={ok ? undefined : 'running'} className="overflow-hidden">
                <TraceIcon>{ok ? done : <Spinner />}</TraceIcon>
                <TraceTitle className="min-w-0 truncate" title={title}>
                  {title}
                </TraceTitle>
                {/* Long tool names and agent step details truncate inside the sidebar instead of widening it. */}
                {detail ? (
                  <TraceDescription className="num min-w-0 shrink truncate" title={detail}>
                    {detail}
                  </TraceDescription>
                ) : null}
                {ok && n.evidence_ids?.length ? (
                  <TraceDescription className="whitespace-nowrap">
                    · {t(lang, 'stream_on_plan', { n: fmtCount(n.evidence_ids.length, lang) })}
                  </TraceDescription>
                ) : null}
              </TraceLine>
            </motion.div>
          )
        })}
        {st?.check?.ranking?.length ? (
          <motion.div {...enter} data-stream-check>
            <TraceLine status="running">
              <TraceIcon>
                <ScanSearchIcon />
              </TraceIcon>
              <TraceTitle>{t(lang, 'stream_checked', { n: fmtCount(st.check.ranking?.length ?? 0, lang) })}</TraceTitle>
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

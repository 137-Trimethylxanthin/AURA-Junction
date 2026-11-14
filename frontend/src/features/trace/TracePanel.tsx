import { useEffect, useMemo, useRef, useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { CheckIcon, ChevronLeftIcon, CopyIcon } from 'lucide-react'
import type { TraceKind, TraceNode } from '@/api/contract'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useDraft } from '@/features/chat/draft'
import { useUi } from '@/i18n/uiLang'
import { cn } from '@/lib/utils'
import { callsText, fmtUsd } from '@/features/chat/view'
import { traceDetail } from './detail'

const ROUTE_KEY: Record<string, 'tr_route_exact' | 'tr_route_cache' | 'tr_route_agent'> = {
  exact: 'tr_route_exact',
  cache: 'tr_route_cache',
  agent: 'tr_route_agent',
}
import { actions, appStore, selectActiveTurn, useAppStore } from '@/store'

const KIND: Record<TraceKind, { dot: string; card: string; conf: string }> = {
  exact: { dot: 'bg-success', card: 'border-success/30 bg-success-surface/50', conf: 'text-success' },
  model: { dot: 'bg-info', card: 'border-border bg-card', conf: 'text-info' },
  warn: { dot: 'bg-warning', card: 'border-warning/30 bg-warning-surface/60', conf: 'text-warning' },
  info: { dot: 'bg-muted-foreground', card: 'border-border bg-card', conf: 'text-muted-foreground' },
}

const KIND_LABEL: Record<TraceKind, string> = { exact: 'Exact step', model: 'Model step', warn: 'Warning', info: 'Info' }

/** Unknown node kinds (newer backends) render as plain info steps. */
const kindOf = (k: string): TraceKind => (k in KIND ? (k as TraceKind) : 'info')

/** Depth-first order with each node's depth, following `parent` links (unknown parents → roots). */
function flattenTrace(nodes: TraceNode[]): { node: TraceNode; depth: number }[] {
  const ids = new Set(nodes.map((n) => n.id))
  const children = new Map<string | undefined, TraceNode[]>()
  for (const n of nodes) {
    const p = n.parent && ids.has(n.parent) && n.parent !== n.id ? n.parent : undefined
    children.set(p, [...(children.get(p) ?? []), n])
  }
  const out: { node: TraceNode; depth: number }[] = []
  const seen = new Set<string>()
  const walk = (p: string | undefined, depth: number) => {
    for (const n of children.get(p) ?? []) {
      if (seen.has(n.id)) continue
      seen.add(n.id)
      out.push({ node: n, depth })
      walk(n.id, depth + 1)
    }
  }
  walk(undefined, 0)
  for (const n of nodes) if (!seen.has(n.id)) out.push({ node: n, depth: 0 })
  return out
}

function confidence(c: TraceNode['confidence']): string | null {
  if (c === undefined) return null
  if (c === 'sample') return 'sample'
  return `${Math.round(c <= 1 ? c * 100 : c)} %`
}

/** Collapsible monospaced SQL of a step, with its row count, columns and time; wraps instead of scrolling the page. */
function SqlBlock({ sql }: { sql: NonNullable<ReturnType<typeof traceDetail>['sql']> }) {
  const { tu } = useUi()
  const [copied, setCopied] = useState(false)
  const stats = [
    sql.rows !== undefined ? tu(sql.rows === 1 ? 'tr_sql_row' : 'tr_sql_rows', { n: sql.rows }) : null,
    sql.columns.length ? tu('tr_sql_columns', { cols: sql.columns.join(', ') }) : null,
    sql.ms !== undefined ? `${sql.ms} ms` : null,
  ].filter(Boolean)
  const copy = () => {
    void navigator.clipboard
      ?.writeText(sql.query)
      .then(() => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 1500)
      })
      .catch(() => undefined)
  }
  return (
    <details className="group text-xs" data-sql>
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-2 gap-y-0.5 text-muted-foreground select-none">
        <Badge variant="outline" className="font-mono">
          {tu('tr_sql')}
        </Badge>
        <span className="num min-w-0 break-words">{stats.join(' · ')}</span>
      </summary>
      <div className="relative mt-1.5">
        <pre className="num max-w-full rounded-md border bg-muted/50 p-2 pr-10 font-mono text-[12px] leading-snug break-words whitespace-pre-wrap">
          {sql.query}
        </pre>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="absolute top-1 right-1 size-8"
          aria-label={copied ? tu('tr_sql_copied') : tu('tr_sql_copy')}
          title={copied ? tu('tr_sql_copied') : tu('tr_sql_copy')}
          onClick={copy}
        >
          {copied ? <CheckIcon /> : <CopyIcon />}
        </Button>
      </div>
    </details>
  )
}

export function TracePanel() {
  const traceOpen = useAppStore((s) => s.ui.traceOpen)
  const turn = useAppStore(selectActiveTurn)
  const question = useAppStore((s) => {
    const u = turn ? s.turns.find((x) => x.id === turn.replyTo) : undefined
    return u && u.role === 'user' ? u.text : ''
  })
  const { lang, tu } = useUi()
  const reduce = useReducedMotion()
  const back = useRef<HTMLButtonElement>(null)
  // A finished answer shows its full trace; a streaming one shows its tool calls as they arrive.
  const trace = turn?.response?.trace ?? turn?.stream?.nodes
  const streaming = turn?.status === 'pending' && !!turn.stream
  const resolved = useMemo(() => new Set(turn?.stream?.resolved ?? []), [turn?.stream?.resolved])
  const nodes = useMemo(() => flattenTrace(trace ?? []), [trace])

  useEffect(() => {
    // Remember who opened the tree so closing it returns focus there.
    const active = document.activeElement
    // (Ignore our own back button: StrictMode re-runs this effect after it has focus.)
    if (active instanceof HTMLElement && active !== document.body && !active.closest('[data-trace-panel]')) opener = active
    needsFocusRestore = true
    back.current?.focus()
    // Capture phase: the tree is the top layer of the sidebar, so one Escape closes only the tree.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      if (document.querySelector('[role="menu"], [role="dialog"]')) return
      e.preventDefault()
      e.stopImmediatePropagation()
      close()
    }
    window.addEventListener('keydown', onKey, true)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      if (!appStore.getState().ui.traceOpen) {
        actions().hover([])
        if (needsFocusRestore) restoreFocusFromTrace()
      }
    }
  }, [])

  // Restore focus immediately when `traceOpen` flips false (e.g. via `T` shortcut) without waiting for AnimatePresence exit.
  useEffect(() => {
    if (!traceOpen) {
      actions().hover([])
      if (needsFocusRestore) restoreFocusFromTrace()
    }
  }, [traceOpen])

  return (
    <motion.section
      aria-label={tu('trace_region')}
      data-trace-panel
      initial={reduce ? { opacity: 0 } : { opacity: 0, x: 24 }}
      animate={reduce ? { opacity: 1 } : { opacity: 1, x: 0 }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, x: 24 }}
      // Critically damped spring: re-targets from the live value, so a quick close after open never jumps.
      transition={reduce ? { duration: 0.12 } : { type: 'spring', bounce: 0, duration: 0.32 }}
      className={cn('flex min-h-0 flex-1 flex-col', !traceOpen && 'pointer-events-none!')}
    >
      <header className="flex items-center gap-2 border-b px-2 py-2">
        <Button ref={back} variant="ghost" onClick={close} aria-label={tu('trace_back')} data-trace-back>
          <ChevronLeftIcon data-icon="inline-start" />
          {tu('trace_chat')}
        </Button>
        <div className="flex min-w-0 flex-1 flex-col">
          <h2 className="text-sm font-semibold">{tu('trace_region')}</h2>
          <p className="truncate text-xs text-muted-foreground" title={question}>
            {question}
          </p>
        </div>
      </header>
      <ol className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto overscroll-contain p-3" onPointerLeave={(e) => e.pointerType === 'mouse' && actions().hover([])}>
        {nodes.length === 0 ? (
          <li className="text-sm text-muted-foreground">{tu('trace_empty')}</li>
        ) : null}
        {nodes.map(({ node, depth }) => {
          const kind = kindOf(node.kind)
          const k = KIND[kind]
          const detail = traceDetail(node)
          const conf = confidence(node.confidence)
          const ids = node.evidence_ids ?? []
          const light = () => actions().hover(ids)
          return (
            <motion.li
              key={node.id}
              style={{ marginLeft: Math.min(depth, 4) * 18 }}
              className={cn(depth > 0 && 'border-l-2 pl-3')}
              // Steps that stream in rise into place; the finished tree renders without delay.
              initial={streaming ? (reduce ? { opacity: 0 } : { opacity: 0, y: 6 }) : false}
              animate={{ opacity: 1, y: 0 }}
              transition={reduce ? { duration: 0.12 } : { type: 'spring', bounce: 0, duration: 0.3 }}
            >
              <div
                tabIndex={0}
                data-trace-node={node.id}
                aria-label={`${detail.decision?.decider === 'gemini-decision-1' ? tu('tr_decision') : KIND_LABEL[kind]}: ${node.title}. ${node.body}${ids.length ? ` Lights ${ids.length} on the plan.` : ''}`}
                onPointerEnter={(e) => e.pointerType === 'mouse' && light()}
                onFocus={light}
                onBlur={() => actions().hover([])}
                className={cn(
                  'flex flex-col gap-1 rounded-lg border px-3 py-2.5 outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring/50',
                  k.card,
                  detail.decision && 'border-primary/40',
                  ids.length > 0 && 'hover:border-info',
                )}
              >
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span aria-hidden className={cn('size-2 shrink-0 rounded-full', k.dot)} />
                  <strong className="text-[13px] font-semibold">{node.title}</strong>
                  <span className="rounded-full bg-muted px-2 py-px text-xs text-muted-foreground">{node.tag}</span>
                  <span className="flex-1" />
                  {conf ? <span className={cn('num text-xs font-semibold', k.conf)}>{conf}</span> : null}
                </div>
                <p className="text-[13px] text-foreground/85">{node.body}</p>
                {detail.decision ? (
                  <div className="flex flex-wrap items-center gap-1.5" data-decision>
                    {detail.decision.decider === 'gemini-decision-1' ? (
                      <Badge>{tu('tr_decision')}</Badge>
                    ) : (
                      <Badge variant="secondary">
                        {detail.decision.decider === 'context' ? tu('tr_context') : tu('tr_router')}
                      </Badge>
                    )}
                    {detail.decision.confidencePct !== undefined ? (
                      <span className="num text-xs font-semibold text-primary">{detail.decision.confidencePct} %</span>
                    ) : null}
                    {detail.decision.route ? (
                      <Badge variant="outline">
                        {ROUTE_KEY[detail.decision.route] ? tu(ROUTE_KEY[detail.decision.route]) : detail.decision.route}
                      </Badge>
                    ) : null}
                    {detail.decision.matched.map((m) => (
                      <Badge key={m} variant="secondary">
                        {m}
                      </Badge>
                    ))}
                    {detail.decision.runnerUp ? (
                      <Badge variant="outline" className="num text-muted-foreground">
                        {tu('tr_runner_up')}: {detail.decision.runnerUp}
                        {detail.decision.runnerUpPct !== undefined ? ` ${detail.decision.runnerUpPct} %` : ''}
                      </Badge>
                    ) : null}
                  </div>
                ) : null}
                {detail.sql ? <SqlBlock sql={detail.sql} /> : null}
                {detail.resources?.grabbed !== undefined ? (
                  <p className="num text-xs text-muted-foreground" data-resources>
                    {tu('tr_resources', {
                      n: detail.resources.grabbed,
                      tables: detail.resources.tables ?? '–',
                      ms: detail.resources.ms ?? 0,
                    })}
                  </p>
                ) : null}
                {detail.filter && (detail.filter.kept !== undefined || detail.filter.dropped !== undefined) ? (
                  <div className="flex flex-col gap-1" data-filter>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {detail.filter.kept !== undefined ? (
                        <Badge variant="secondary" className="num">
                          {tu('tr_kept', { n: detail.filter.kept })}
                        </Badge>
                      ) : null}
                      {detail.filter.dropped !== undefined ? (
                        <Badge variant="outline" className="num text-muted-foreground">
                          {tu('tr_dropped', { n: detail.filter.dropped })}
                        </Badge>
                      ) : null}
                      {detail.filter.rule ? (
                        <span className="text-xs text-muted-foreground">{tu('tr_rule', { rule: detail.filter.rule })}</span>
                      ) : null}
                    </div>
                    {detail.filter.reasons.length > 0 ? (
                      <details className="text-xs text-muted-foreground">
                        <summary className="cursor-pointer select-none">{tu('tr_why_dropped')}</summary>
                        <ul className="num mt-1 flex flex-col gap-0.5 pl-3">
                          {detail.filter.reasons.map((r) => (
                            <li key={r.id}>
                              {r.id}
                              {r.reason ? `: ${r.reason}` : ''}
                            </li>
                          ))}
                        </ul>
                      </details>
                    ) : null}
                    {detail.filter.keptIds.length > 0 ? (
                      <details className="text-xs text-muted-foreground" data-kept-ids>
                        <summary className="cursor-pointer select-none">
                          {tu('tr_kept_ids', { n: detail.filter.keptIds.length })}
                        </summary>
                        <p className="num mt-1 break-words">
                          {detail.filter.keptIds.slice(0, 60).join(', ')}
                          {detail.filter.keptIds.length > 60 ? ', …' : ''}
                        </p>
                      </details>
                    ) : null}
                  </div>
                ) : null}
                {detail.answer ? (
                  <p className="num text-xs text-muted-foreground" data-answer>
                    {[
                      detail.answer.source === 'sql' ? tu('tr_sql') : detail.answer.source,
                      detail.answer.calls !== undefined ? callsText(detail.answer.calls, lang) : null,
                      detail.answer.cost !== undefined ? fmtUsd(detail.answer.cost) : null,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </p>
                ) : null}
                {node.tool ? (
                  <p className="num text-xs text-muted-foreground" data-tool={node.tool.name}>
                    {node.tool.name}
                    {streaming && !resolved.has(node.id) ? (
                      <span className="shimmer ml-1.5" data-running>
                        running…
                      </span>
                    ) : (
                      <> · {node.tool.ms} ms</>
                    )}
                  </p>
                ) : null}
                {ids.length ? <p className="num text-xs text-muted-foreground">{ids.length} on the plan</p> : null}
              </div>
            </motion.li>
          )
        })}
      </ol>
      <footer className="border-t bg-muted/50 px-3 py-2.5 text-xs text-muted-foreground">
        Point at a step to light what it used on the plan. Geometry steps are exact; the Gemini check covers the drawn boxes only.
      </footer>
    </motion.section>
  )
}

/** Element that opened the tree (a card's Reasoning tree button or the session button). */
let opener: HTMLElement | null = null
let needsFocusRestore = false

function restoreFocusFromTrace() {
  needsFocusRestore = false
  const target = opener
  opener = null
  requestAnimationFrame(() => {
    if (document.querySelector('[role="menu"], [role="dialog"]')) return
    if (target?.isConnected) target.focus()
    else useDraft.getState().focus()
  })
}

function close() {
  actions().hover([])
  actions().setTraceOpen(false)
  restoreFocusFromTrace()
}

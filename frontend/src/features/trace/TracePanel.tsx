import { useEffect, useMemo, useRef } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { ChevronLeftIcon } from 'lucide-react'
import type { TraceKind, TraceNode } from '@/api/contract'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { actions, selectActiveTurn, useAppStore } from '@/store'

const KIND: Record<TraceKind, { dot: string; card: string; conf: string }> = {
  exact: { dot: 'bg-success', card: 'border-success/30 bg-success-surface/50', conf: 'text-success' },
  model: { dot: 'bg-info', card: 'border-border bg-card', conf: 'text-info' },
  warn: { dot: 'bg-warning', card: 'border-warning/30 bg-warning-surface/60', conf: 'text-warning' },
  info: { dot: 'bg-muted-foreground', card: 'border-border bg-card', conf: 'text-muted-foreground' },
}

const KIND_LABEL: Record<TraceKind, string> = { exact: 'Exact step', model: 'Model step', warn: 'Warning', info: 'Info' }

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

export function TracePanel() {
  const turn = useAppStore(selectActiveTurn)
  const question = useAppStore((s) => {
    const u = turn ? s.turns.find((x) => x.id === turn.replyTo) : undefined
    return u && u.role === 'user' ? u.text : ''
  })
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
    return () => window.removeEventListener('keydown', onKey, true)
  }, [])

  return (
    <motion.section
      aria-label="Reasoning tree"
      data-trace-panel
      initial={reduce ? { opacity: 0 } : { opacity: 0, x: 24 }}
      animate={reduce ? { opacity: 1 } : { opacity: 1, x: 0 }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, x: 24 }}
      // Critically damped spring: re-targets from the live value, so a quick close after open never jumps.
      transition={reduce ? { duration: 0.12 } : { type: 'spring', bounce: 0, duration: 0.32 }}
      className="flex min-h-0 flex-1 flex-col"
    >
      <header className="flex items-center gap-2 border-b px-2 py-2">
        <Button ref={back} variant="ghost" onClick={close} aria-label="Back to chat (Escape)">
          <ChevronLeftIcon data-icon="inline-start" />
          Chat
        </Button>
        <div className="flex min-w-0 flex-1 flex-col">
          <h2 className="text-sm font-semibold">Reasoning tree</h2>
          <p className="truncate text-xs text-muted-foreground" title={question}>
            {question}
          </p>
        </div>
      </header>
      <ol className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto overscroll-contain p-3" onPointerLeave={(e) => e.pointerType === 'mouse' && actions().hover([])}>
        {nodes.length === 0 ? (
          <li className="text-sm text-muted-foreground">No reasoning steps for this answer yet.</li>
        ) : null}
        {nodes.map(({ node, depth }) => {
          const k = KIND[node.kind]
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
                aria-label={`${KIND_LABEL[node.kind]}: ${node.title}. ${node.body}${ids.length ? ` Lights ${ids.length} on the plan.` : ''}`}
                onPointerEnter={(e) => e.pointerType === 'mouse' && light()}
                onFocus={light}
                onBlur={() => actions().hover([])}
                className={cn(
                  'flex flex-col gap-1 rounded-lg border px-3 py-2.5 outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring/50',
                  k.card,
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

function close() {
  actions().hover([])
  actions().setTraceOpen(false)
  const target = opener
  opener = null
  requestAnimationFrame(() => {
    if (target?.isConnected) target.focus()
    else document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Ask about this drawing"]')?.focus()
  })
}

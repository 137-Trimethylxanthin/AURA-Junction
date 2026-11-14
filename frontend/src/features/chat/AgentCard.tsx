import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useReducedMotion } from 'motion/react'
import {
  CheckIcon,
  ChevronDownIcon,
  CircleAlertIcon,
  EyeIcon,
  GitBranchIcon,
  MinusIcon,
  PlusIcon,
  RotateCcwIcon,
  XIcon,
} from 'lucide-react'
import type { Lang } from '@/api/contract'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { t } from '@/i18n/strings'
import { fmtCount, noun } from '@/lib/format'
import { cn } from '@/lib/utils'
import { actions, selectDrawing, useAppStore, useDerived, type AgentTurn } from '@/store'
import { StreamSteps } from './StreamSteps'
import { chips, footerParts, rows, sentence, valueParts, type Chip, type Row } from './view'

const CHIP_TONE: Record<Chip['tone'], string> = {
  success: 'bg-success-surface text-success',
  info: 'bg-info-surface text-info',
  warning: 'bg-warning-surface text-warning',
  destructive: 'bg-destructive-surface text-destructive',
  neutral: 'bg-muted text-foreground',
}

const CHIP_OUTLINE: Record<Chip['tone'], string> = {
  success: 'border-success/40 text-success',
  info: 'border-info/40 text-info',
  warning: 'border-warning/40 text-warning',
  destructive: 'border-destructive/40 text-destructive',
  neutral: 'border-dashed border-input text-muted-foreground',
}

/** Hover highlights only for a real mouse (no sticky highlight after a tap). */
const mouseOnly = (fn: () => void) => (e: React.PointerEvent) => {
  if (e.pointerType === 'mouse') fn()
}

function ChipPill({ chip, turnId }: { chip: Chip; turnId: string }) {
  if (chip.evidenceId) {
    const id = chip.evidenceId
    // The least-certain chip locates its box on the plan.
    return (
      <button
        type="button"
        title={chip.title}
        data-chip={chip.key}
        data-tone={chip.tone}
        onClick={() => actions().select(turnId, id)}
        className={cn(
          'relative inline-flex min-h-6 max-w-full items-center gap-1 rounded-2xl border bg-transparent px-2.5 py-0.5 text-left text-xs font-medium [overflow-wrap:anywhere] transition-colors',
          // 40 px hit area without growing the chip.
          'before:absolute before:inset-x-0 before:top-1/2 before:min-h-10 before:-translate-y-1/2',
          '[@media(hover:hover)]:hover:bg-info-surface',
          CHIP_OUTLINE[chip.tone],
        )}
      >
        {chip.text}
      </button>
    )
  }
  return (
    <span
      title={chip.title}
      data-chip={chip.key}
      data-tone={chip.tone}
      className={cn(
        'inline-flex min-h-6 max-w-full items-center gap-1 rounded-2xl px-2.5 py-0.5 text-xs font-medium [overflow-wrap:anywhere]',
        chip.outline ? cn('border bg-transparent', CHIP_OUTLINE[chip.tone]) : CHIP_TONE[chip.tone],
      )}
    >
      {chip.tone === 'destructive' ? <CircleAlertIcon className="size-3.5 shrink-0" aria-hidden /> : null}
      {chip.text}
    </span>
  )
}

function EvidenceRow({ turnId, row, active }: { turnId: string; row: Row; active: boolean }) {
  const selected = useAppStore((s) => active && s.ui.selectedEvidenceId === row.id)
  const hovered = useAppStore((s) => active && s.ui.hoveredEvidenceIds.includes(row.id))
  const Toggle = row.kind === 'room' ? (row.on ? MinusIcon : PlusIcon) : row.on ? XIcon : RotateCcwIcon
  const toggleLabel =
    row.kind === 'room'
      ? `${row.on ? 'Leave out' : 'Add'} ${row.title} ${row.badge}`
      : `${row.on ? 'Reject' : 'Restore'} ${row.title}`
  const subParts = row.sub ? row.sub.split(' · ') : []
  return (
    <li
      data-evidence-id={row.id}
      data-state={row.on ? 'on' : 'off'}
      className={cn(
        'flex scroll-my-2 items-center gap-1 rounded-lg p-0.5 transition-colors',
        row.edited && 'bg-user-surface',
        (hovered || selected) && 'bg-info-surface',
      )}
      onPointerEnter={active ? mouseOnly(() => actions().hover([row.id])) : undefined}
      onPointerLeave={active ? mouseOnly(() => actions().hover([])) : undefined}
    >
      <button
        type="button"
        aria-current={selected || undefined}
        aria-label={`Show ${row.title} on the plan`}
        title={[row.title, row.sub].filter(Boolean).join('\n')}
        onClick={() => actions().select(turnId, row.id)}
        className="flex min-h-10 min-w-0 flex-1 items-center gap-2.5 rounded-md px-1.5 py-1 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <span
          title={row.badge}
          className={cn(
            'num inline-flex h-6 max-w-24 min-w-7 shrink-0 items-center justify-center rounded-md px-1.5 text-xs font-semibold',
            row.on ? 'bg-info text-primary-foreground' : 'border-[1.5px] border-dashed border-warning text-warning',
          )}
        >
          <span className="truncate">{row.badge}</span>
        </span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span
            className={cn(
              'line-clamp-2 text-sm font-medium [overflow-wrap:anywhere]',
              !row.on && 'text-muted-foreground line-through',
            )}
          >
            {row.title}
          </span>
          {subParts.length ? (
            <span className="num line-clamp-2 text-xs text-muted-foreground [overflow-wrap:anywhere]">
              {subParts.map((p, i) => (
                <span key={i} className={i === 0 ? 'whitespace-nowrap' : undefined}>
                  {i > 0 ? ' · ' : ''}
                  {p}
                </span>
              ))}
            </span>
          ) : null}
        </span>
        <span className={cn('shrink-0 text-xs font-medium', row.on ? 'text-success' : 'text-warning')}>{row.state}</span>
      </button>
      <Button
        variant="outline"
        size="icon"
        aria-label={toggleLabel}
        title={toggleLabel}
        onClick={() => actions().toggle(turnId, row.id)}
      >
        <Toggle />
      </Button>
    </li>
  )
}

function EvidenceList({ turn, lang, active }: { turn: AgentTurn; lang: Lang; active: boolean }) {
  const drawing = useAppStore(selectDrawing)
  const resp = turn.response!
  const list = useMemo(() => rows(resp, turn.toggles, drawing, lang), [resp, turn.toggles, drawing, lang])
  const ref = useRef<HTMLUListElement>(null)
  const selectedId = useAppStore((s) => (active ? s.ui.selectedEvidenceId : null))
  const reduce = useReducedMotion()

  // A box tapped on the plan: open the list and bring its row into view.
  useEffect(() => {
    if (!selectedId || !list.some((r) => r.id === selectedId)) return
    if (!turn.listOpen) {
      actions().setListOpen(turn.id, true)
      return
    }
    const el = ref.current?.querySelector<HTMLElement>(`[data-evidence-id="${CSS.escape(selectedId)}"]`)
    el?.scrollIntoView({ block: 'nearest', behavior: reduce ? 'auto' : 'smooth' })
  }, [selectedId, turn.listOpen, turn.id, list, reduce])

  if (!list.length) return null
  const isArea = resp.answer.kind === 'area'
  const label = turn.listOpen
    ? t(lang, isArea ? 'hide_arithmetic' : 'hide_evidence')
    : t(lang, isArea ? 'arithmetic_n' : 'evidence_n', { n: list.length })
  const listId = `ev-${turn.id}`
  const subject = resp.evidence.boxes[0]?.subject ?? 'room'
  const summary = t(lang, 'summary_boxes', { n: `${fmtCount(list.length, lang)} ${noun(subject, list.length, lang)}` })
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          className="-ml-2 shrink-0 text-muted-foreground"
          aria-expanded={turn.listOpen}
          aria-controls={listId}
          onClick={() => actions().setListOpen(turn.id, !turn.listOpen)}
        >
          <ChevronDownIcon data-icon="inline-start" className={cn('transition-transform', !turn.listOpen && '-rotate-90')} />
          {label}
        </Button>
        {turn.listOpen ? <span className="ml-auto truncate text-xs text-muted-foreground">{summary}</span> : null}
      </div>
      {turn.listOpen ? (
        <CappedScroll label={`${label}: ${summary}`}>
          <ul id={listId} ref={ref} className="flex flex-col gap-0.5" aria-label={label}>
            {list.map((r) => (
              <EvidenceRow key={r.id} turnId={turn.id} row={r} active={active} />
            ))}
          </ul>
        </CappedScroll>
      ) : null}
    </div>
  )
}

/** About four rows tall, scrolls inside the card; fades at an edge that hides rows; keyboard-scrollable. */
function CappedScroll({ children, label }: { children: React.ReactNode; label: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ top: false, bottom: false })
  const measure = useCallback(() => {
    const el = ref.current
    if (!el) return
    const top = el.scrollTop > 2
    const bottom = el.scrollTop + el.clientHeight < el.scrollHeight - 2
    setEdges((p) => (p.top === top && p.bottom === bottom ? p : { top, bottom }))
  }, [])
  useEffect(() => {
    const el = ref.current
    if (!el) return
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    if (el.firstElementChild) ro.observe(el.firstElementChild)
    return () => ro.disconnect()
  }, [measure])
  const fade = '1.75rem'
  const mask =
    edges.top || edges.bottom
      ? `linear-gradient(to bottom, ${edges.top ? `transparent, black ${fade}` : 'black'}, ${edges.bottom ? `black calc(100% - ${fade}), transparent` : 'black'})`
      : undefined
  const scrollable = edges.top || edges.bottom
  return (
    <div className="relative border-t pt-1.5">
      <div
        ref={ref}
        onScroll={measure}
        tabIndex={scrollable ? 0 : undefined}
        role={scrollable ? 'region' : undefined}
        aria-label={scrollable ? label : undefined}
        data-evidence-scroll
        className="max-h-[11.75rem] overflow-y-auto overscroll-contain rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        style={{ maskImage: mask, WebkitMaskImage: mask }}
      >
        {children}
      </div>
      {edges.bottom ? (
        <ChevronDownIcon
          aria-hidden
          className="pointer-events-none absolute bottom-0 left-1/2 size-4 -translate-x-1/2 text-muted-foreground"
        />
      ) : null}
    </div>
  )
}

function Footer({ parts }: { parts: string[] }) {
  return (
    <p className="num flex flex-wrap gap-x-1.5 border-t border-dashed pt-2 text-xs text-muted-foreground" data-footer>
      {parts.map((part, i) => (
        <span key={i} className="whitespace-nowrap">
          {i > 0 ? '· ' : ''}
          {part}
        </span>
      ))}
    </p>
  )
}

function Feedback({ turn, lang }: { turn: AgentTurn; lang: Lang }) {
  if (!turn.feedback) return null
  return (
    <p className="text-xs text-muted-foreground" role="status">
      {t(lang, turn.feedback === 'up' ? 'thanks_up' : 'thanks_down')}
    </p>
  )
}

function Actions({ turn, active, lang }: { turn: AgentTurn; active: boolean; lang: Lang }) {
  return (
    // One row always: below ~22rem of card width the two text buttons drop to icons (names stay in aria-label/title).
    <div className="@container flex flex-nowrap items-center gap-1">
      {!active ? (
        <Button
          variant="outline"
          className="min-w-10 px-2.5"
          aria-label={t(lang, 'show_on_plan')}
          title={t(lang, 'show_on_plan')}
          onClick={() => actions().setActive(turn.id)}
        >
          <EyeIcon data-icon="inline-start" />
          <span className="hidden @[21rem]:inline">{t(lang, 'show_on_plan')}</span>
        </Button>
      ) : null}
      <Button
        variant="outline"
        className="min-w-10 px-2.5"
        aria-label={t(lang, 'reasoning_tree')}
        title={t(lang, 'reasoning_tree')}
        data-trace-opener
        onClick={() => {
          // setActive clears the selection; keep it when this card is already on the plan.
          if (!active) actions().setActive(turn.id)
          actions().setTraceOpen(true)
        }}
      >
        <GitBranchIcon data-icon="inline-start" />
        <span className={active ? 'hidden @[13rem]:inline' : 'hidden @[21rem]:inline'}>{t(lang, 'reasoning_tree')}</span>
      </Button>
      <span className="flex-1" />
      {!turn.feedback ? (
        <span className="flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            aria-label="This answer is right"
            title="This answer is right"
            onClick={() => actions().setFeedback(turn.id, 'up')}
          >
            <CheckIcon />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Something is wrong"
            title="Something is wrong"
            onClick={() => actions().setFeedback(turn.id, 'down')}
          >
            <CircleAlertIcon />
          </Button>
        </span>
      ) : null}
    </div>
  )
}

function CardShell({
  active,
  tone,
  children,
  turnId,
  lang,
}: {
  active: boolean
  tone?: 'error'
  children: React.ReactNode
  turnId: string
  lang: Lang
}) {
  return (
    <article
      data-turn={turnId}
      data-active={active || undefined}
      aria-label="Answer"
      lang={lang}
      className={cn(
        'flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-4 text-card-foreground transition-[border-color,box-shadow] duration-200',
        active && 'border-info shadow-[0_0_0_1px_var(--info)]',
        tone === 'error' && 'border-destructive/40 bg-destructive-surface shadow-none',
      )}
    >
      {children}
    </article>
  )
}

export function AgentCard({ turn, lang: userLang }: { turn: AgentTurn; lang: Lang }) {
  const active = useAppStore((s) => s.ui.activeTurnId === turn.id)
  const derived = useDerived(turn.id)
  const drawing = useAppStore(selectDrawing)
  const resp = turn.response
  const lang = resp?.detected_lang ?? userLang

  if (turn.status === 'pending')
    return (
      <CardShell active={active} turnId={turn.id} lang={userLang}>
        <div className="flex items-baseline gap-2" aria-busy="true">
          <Skeleton aria-hidden className="h-11 w-20" />
          <Skeleton aria-hidden className="h-5 w-24" />
        </div>
        {turn.stream ? (
          <StreamSteps turn={turn} lang={userLang} />
        ) : (
          <>
            <Skeleton aria-hidden className="h-4 w-3/4" />
            <p className="text-sm text-muted-foreground">{t(userLang, 'pending')}</p>
          </>
        )}
      </CardShell>
    )

  if (turn.status === 'error' || !resp || !derived)
    return (
      <CardShell active={false} tone="error" turnId={turn.id} lang={userLang}>
        <div className="flex min-w-0 items-start gap-2 text-destructive">
          <CircleAlertIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
          <div className="flex min-w-0 flex-col gap-1">
            <strong className="text-sm font-semibold">{t(userLang, 'couldnt_answer')}</strong>
            <p className="min-w-0 text-sm text-foreground [overflow-wrap:anywhere]">
              {turn.error?.message ?? t(userLang, 'something_wrong')}
            </p>
          </div>
        </div>
      </CardShell>
    )

  const a = resp.answer
  if (a.kind === 'abstain') {
    const title =
      a.reason === 'not_on_drawing'
        ? t(lang, 'not_on_drawing')
        : a.reason === 'not_supported_yet'
          ? t(lang, 'not_supported_yet')
          : t(lang, 'unclear_question')
    const body =
      a.reason === 'unclear_question'
        ? t(lang, 'unclear_hint')
        : [a.where_to_find, t(lang, 'nothing_guessed')].filter(Boolean).join(' ')
    return (
      <CardShell active={active} turnId={turn.id} lang={lang}>
        <div className="flex flex-col gap-1.5" data-abstain={a.reason}>
          <h3 className="text-lg leading-snug font-semibold text-balance" data-abstain-title>
            {title}
          </h3>
          <p className="text-sm text-muted-foreground [overflow-wrap:anywhere]" data-abstain-body>
            {body}
          </p>
        </div>
        <Actions turn={turn} active={active} lang={lang} />
        <Feedback turn={turn} lang={lang} />
        <Footer parts={footerParts(resp, lang)} />
      </CardShell>
    )
  }

  const parts = valueParts(resp, derived, lang)
  const chipList = chips(resp, derived, lang)
  return (
    <CardShell active={active} turnId={turn.id} lang={lang}>
      <div className="flex flex-col gap-1.5">
        {parts ? (
          <p className="flex flex-wrap items-baseline gap-x-2" data-answer-value={parts[0]}>
            <span className="text-[44px] leading-none font-semibold tracking-tight tabular-nums [overflow-wrap:anywhere]">
              {parts[0]}
            </span>
            <span className="text-lg font-medium">{parts[1]}</span>
          </p>
        ) : null}
        <p className="text-sm text-muted-foreground">{sentence(resp, derived, drawing, lang)}</p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {chipList.map((c) => (
          <ChipPill key={c.key} chip={c} turnId={turn.id} />
        ))}
      </div>
      {derived.changed ? (
        <div
          role="status"
          className="flex items-center gap-2 rounded-lg border border-warning/30 bg-warning-surface py-1 pr-1 pl-3 text-sm text-warning"
        >
          <span className="min-w-0 flex-1">
            {derived.rejected.length > 0 && resp.answer.kind === 'count' && resp.evidence.boxes.length > 0
              ? t(lang, 'you_rejected', {
                  n: derived.rejected.length,
                  total: derived.original ?? '',
                })
              : t(lang, 'you_changed_rooms')}
          </span>
          <Button variant="outline" onClick={() => actions().resetTurn(turn.id)}>
            <RotateCcwIcon data-icon="inline-start" />
            {t(lang, 'undo')}
          </Button>
        </div>
      ) : null}
      <EvidenceList turn={turn} lang={lang} active={active} />
      <Actions turn={turn} active={active} lang={lang} />
      <Feedback turn={turn} lang={lang} />
      <Footer parts={footerParts(resp, lang)} />
    </CardShell>
  )
}

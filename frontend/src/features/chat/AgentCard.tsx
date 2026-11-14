import { useUi } from '@/i18n/uiLang'
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { useReducedMotion } from 'motion/react'
import {
  ArrowUpRightIcon,
  CheckIcon,
  ChevronDownIcon,
  CircleAlertIcon,
  CloudOffIcon,
  SquareIcon,
  TimerOffIcon,
  EyeIcon,
  GitBranchIcon,
  MinusIcon,
  PlusIcon,
  RotateCcwIcon,
  XIcon,
} from 'lucide-react'
import type { Drawing, Lang } from '@/api/contract'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { t, type StringKey, type UiKey } from '@/i18n/strings'
import { fmtCount, noun } from '@/lib/format'
import { cn } from '@/lib/utils'
import {
  actions,
  selectActiveProject,
  selectDrawing,
  selectDrawingById,
  selectFocusedGroupId,
  selectIsPending,
  useAppStore,
  useDerived,
  type AgentTurn,
} from '@/store'
import { BoxCropThumb } from './BoxCropThumb'
import { StreamSteps } from './StreamSteps'
import { ListenButton } from '@/features/voice/ListenButton'
import {
  agentFailure,
  chips,
  evidenceGroupsOf,
  footerParts,
  groupCssVars,
  rows,
  sentence,
  metricText,
  triedText,
  valueParts,
  type AgentFailure,
  type Chip,
  type EvidenceGroupItem,
  type Row,
} from './view'

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

/** A counted element with no box on the plan (IFC): listed for checking, nothing to select or toggle. */
function ElementRow({ row }: { row: Row }) {
  return (
    <li data-evidence-id={row.id} data-evidence-kind="element" className="flex min-h-10 items-center gap-2.5 rounded-lg px-2 py-1" title={row.hint}>
      <span className="num inline-flex h-6 max-w-24 min-w-7 shrink-0 items-center justify-center rounded-md border border-border px-1.5 text-xs font-semibold text-muted-foreground">
        <span className="truncate">{row.badge}</span>
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="line-clamp-2 text-sm font-medium [overflow-wrap:anywhere]">{row.title}</span>
        <span className="num truncate text-xs text-muted-foreground">{row.sub}</span>
      </span>
      <span className="shrink-0 text-xs font-medium text-success">{row.state}</span>
    </li>
  )
}

function setFocusedGroupSafe(id: string | null) {
  actions().setFocusedGroup(id)
}

function GroupLegend({
  groups,
  turn,
  active,
  drawingId,
  floorLabels,
}: {
  groups: EvidenceGroupItem[]
  turn: AgentTurn
  active: boolean
  drawingId: string
  floorLabels: ReadonlyMap<string, string>
}) {
  const { tu } = useUi()
  const navigate = useNavigate()
  const focusedGroupId = useAppStore((s) => (active ? selectFocusedGroupId(s) : null))
  const [hoverPreview, setHoverPreview] = useState<{ hoveredId: string; restoreId: string | null } | null>(null)
  if (!groups.length) return null

  const memberIdsOf = (gid: string, preferDrawingId?: string): string[] => {
    const ev = turn.response?.evidence
    if (!ev) return []
    const preferred: string[] = []
    const rest: string[] = []
    for (const b of ev.boxes) {
      if (b.group_id !== gid) continue
      const bFloor = b.drawing_id ?? drawingId
      if (preferDrawingId && bFloor === preferDrawingId) preferred.push(b.id)
      else rest.push(b.id)
    }
    for (const r of ev.rooms) {
      if (r.group_id !== gid) continue
      const rFloor = r.drawing_id ?? drawingId
      const id = `room-${r.room_id}`
      if (preferDrawingId && rFloor === preferDrawingId) preferred.push(id)
      else rest.push(id)
    }
    return [...preferred, ...rest]
  }

  const pinnedId = hoverPreview ? hoverPreview.restoreId : focusedGroupId

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1.5" role="group" aria-label={tu('group_legend_aria')} data-evidence-groups>
      {groups.map((g) => {
        const vars = groupCssVars(g.color)
        const isPinned = pinnedId === g.id
        const isFocused = focusedGroupId === g.id
        const targetFloor =
          g.target_drawing_id && g.target_drawing_id !== drawingId
            ? (floorLabels.get(g.target_drawing_id) ?? g.target_drawing_id)
            : null
        return (
          <button
            key={g.id}
            type="button"
            data-group-pill={g.id}
            data-group-color={g.color}
            aria-pressed={isPinned}
            onPointerEnter={
              active
                ? mouseOnly(() => {
                    const restoreId = hoverPreview ? hoverPreview.restoreId : focusedGroupId
                    setHoverPreview({ hoveredId: g.id, restoreId })
                    setFocusedGroupSafe(g.id)
                    const ids = memberIdsOf(g.id, drawingId)
                    if (ids.length) actions().hover(ids)
                  })
                : undefined
            }
            onPointerLeave={
              active
                ? mouseOnly(() => {
                    if (hoverPreview) {
                      setFocusedGroupSafe(hoverPreview.restoreId)
                      setHoverPreview(null)
                    }
                    actions().hover([])
                  })
                : undefined
            }
            onClick={() => {
              if (!active) actions().setActive(turn.id)
              const curPinned = hoverPreview ? hoverPreview.restoreId : focusedGroupId
              const nextPinned = curPinned === g.id ? null : g.id
              setHoverPreview(null)
              setFocusedGroupSafe(nextPinned)
              if (nextPinned) {
                const destFloor =
                  g.target_drawing_id && g.target_drawing_id !== drawingId ? g.target_drawing_id : drawingId
                const ids = memberIdsOf(g.id, destFloor)
                if (ids[0]) actions().select(turn.id, ids[0])
                if (destFloor !== drawingId) {
                  void navigate(`/d/${encodeURIComponent(destFloor)}`)
                }
              }
            }}
            style={
              vars
                ? {
                    backgroundColor: isFocused ? vars.solid : vars.surface,
                    color: isFocused ? 'var(--primary-foreground)' : 'var(--foreground)',
                    borderColor: vars.solid,
                  }
                : undefined
            }
            className={cn(
              'inline-flex min-h-10 min-w-10 max-w-full items-center gap-1.5 overflow-hidden rounded-lg border px-2.5 py-1 text-left text-xs font-semibold transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50',
              isFocused && 'shadow-xs',
            )}
          >
            <span
              aria-hidden="true"
              className="size-2.5 shrink-0 rounded-full ring-2 ring-background"
              style={vars ? { backgroundColor: isFocused ? 'var(--primary-foreground)' : vars.solid } : undefined}
            />
            <span className="min-w-0 truncate">{g.label}</span>
            {targetFloor ? (
              <span className="inline-flex max-w-28 shrink-0 items-center gap-0.5 opacity-85">
                <ArrowUpRightIcon className="size-3 shrink-0" aria-hidden="true" />
                <span className="num truncate">{targetFloor}</span>
              </span>
            ) : null}
          </button>
        )
      })}
    </div>
  )
}

function EvidenceRow({
  turnId,
  row,
  active,
  drawing,
  canToggle,
}: {
  turnId: string
  row: Row
  active: boolean
  drawing: Drawing
  canToggle: boolean
}) {
  const { tu } = useUi()
  const navigate = useNavigate()
  const selected = useAppStore((s) => active && s.ui.selectedEvidenceId === row.id)
  const hovered = useAppStore((s) => active && s.ui.hoveredEvidenceIds.includes(row.id))
  const Toggle = row.kind === 'room' ? (row.on ? MinusIcon : PlusIcon) : row.on ? XIcon : RotateCcwIcon
  const toggleLabel =
    row.kind === 'room'
      ? `${row.on ? 'Leave out' : 'Add'} ${row.title} ${row.badge}`
      : `${row.on ? tu('reject_box') : tu('restore_box')} ${row.title}`
  const subParts = row.sub ? row.sub.split(' · ') : []
  const grpVars = groupCssVars(row.groupColor)
  const showCrop = row.kind === 'box' && Boolean(row.box) && (selected || hovered || Boolean(row.leastCertain))
  const isCrossFloor = Boolean(row.drawingId && row.drawingId !== drawing.id)
  const targetDrawing = useAppStore((s) =>
    isCrossFloor && row.drawingId ? selectDrawingById(s)[row.drawingId] : undefined,
  )

  return (
    <li
      data-evidence-id={row.id}
      data-state={row.on ? 'on' : 'off'}
      data-group-id={row.groupId}
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
        aria-label={tu('ev_show', { title: row.floorLabel ? `${row.title} (${row.floorLabel})` : row.title })}
        title={[row.title, row.floorLabel ? `[${row.floorLabel}]` : '', row.sub, row.note].filter(Boolean).join('\n')}
        onClick={() => {
          actions().select(turnId, row.id)
          if (isCrossFloor && row.drawingId) {
            void navigate(`/d/${encodeURIComponent(row.drawingId)}`)
          }
        }}
        className="flex min-h-10 min-w-0 flex-1 items-center gap-2.5 rounded-md px-1.5 py-1 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <span
          title={row.badge}
          style={row.on && grpVars ? { backgroundColor: grpVars.solid, color: 'var(--primary-foreground)' } : undefined}
          className={cn(
            'num inline-flex h-6 max-w-24 min-w-7 shrink-0 items-center justify-center rounded-md px-1.5 text-xs font-semibold',
            row.on
              ? cn(!grpVars && 'bg-info text-primary-foreground', grpVars && 'ring-2 ring-background shadow-xs')
              : 'border-[1.5px] border-dashed border-warning text-warning',
          )}
        >
          <span className="truncate">{row.badge}</span>
        </span>
        {showCrop && row.box ? (
          <BoxCropThumb
            box={row.box}
            drawing={drawing}
            targetDrawing={targetDrawing}
            boxDrawingId={row.drawingId}
            on={row.on}
            accentColor={grpVars?.solid}
          />
        ) : null}
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="flex min-w-0 items-center gap-1.5">
            <span
              className={cn(
                'line-clamp-2 text-sm font-medium [overflow-wrap:anywhere]',
                !row.on && 'text-muted-foreground line-through',
              )}
            >
              {row.title}
            </span>
            {row.floorLabel ? (
              <span
                data-floor-badge={row.drawingId}
                className="num inline-flex max-w-24 shrink-0 items-center gap-0.5 rounded border border-border bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground"
              >
                <span className="truncate">{row.floorLabel}</span>
                <ArrowUpRightIcon className="size-3 shrink-0" aria-hidden="true" />
              </span>
            ) : null}
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
          {row.note ? (
            <span className="num truncate text-xs text-muted-foreground" title={row.note} data-evidence-note>
              {row.note}
            </span>
          ) : null}
        </span>
        <span className={cn('shrink-0 text-xs font-medium', row.on ? 'text-success' : 'text-warning')}>{row.state}</span>
      </button>
      {canToggle ? (
        <Button
          variant="outline"
          size="icon"
          aria-label={toggleLabel}
          title={toggleLabel}
          onClick={() => actions().toggle(turnId, row.id)}
        >
          <Toggle />
        </Button>
      ) : null}
    </li>
  )
}

function EvidenceList({
  turn,
  lang,
  active,
  floorLabels,
}: {
  turn: AgentTurn
  lang: Lang
  active: boolean
  floorLabels: ReadonlyMap<string, string>
}) {
  const drawing = useAppStore(selectDrawing)
  const resp = turn.response!
  const list = useMemo(() => rows(resp, turn.toggles, drawing, lang, floorLabels), [resp, turn.toggles, drawing, lang, floorLabels])
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
    el?.scrollIntoView?.({ block: 'nearest', behavior: reduce ? 'auto' : 'smooth' })
  }, [selectedId, turn.listOpen, turn.id, list, reduce])

  if (!list.length) return null
  const isArea = resp.answer.kind === 'area'
  const label = turn.listOpen
    ? t(lang, isArea ? 'hide_arithmetic' : 'hide_evidence')
    : t(lang, isArea ? 'arithmetic_n' : 'evidence_n', { n: list.length })
  const listId = `ev-${turn.id}`
  const subject = resp.evidence.boxes[0]?.subject ?? 'room'
  const onlyElements = list.every((r) => r.kind === 'element')
  const summary = onlyElements
    ? t(lang, 'rows_n', { n: fmtCount(list.length, lang) })
    : t(lang, 'summary_boxes', { n: `${fmtCount(list.length, lang)} ${noun(subject, list.length, lang)}` })
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
        {turn.listOpen && !onlyElements ? (
          <span className="ml-auto truncate text-xs text-muted-foreground">{summary}</span>
        ) : null}
      </div>
      {turn.listOpen ? (
        <CappedScroll label={`${label}: ${summary}`} lang={lang} totalCount={list.length}>
          <ul id={listId} ref={ref} className="flex flex-col gap-0.5" aria-label={label}>
            {list.map((r) =>
              r.kind === 'element' ? (
                <ElementRow key={r.id} row={r} />
              ) : (
                <EvidenceRow
                  key={r.id}
                  turnId={turn.id}
                  row={r}
                  active={active}
                  drawing={drawing}
                  canToggle={!(resp.method === 'agent_estimate' && (resp.answer.kind !== 'count' || r.kind !== 'box'))}
                />
              ),
            )}
          </ul>
        </CappedScroll>
      ) : null}
    </div>
  )
}

/** About four rows tall, scrolls inside the card; fades at an edge that hides rows; keyboard-scrollable. */
/** Tallest the evidence list gets before it scrolls (about four rows). */
const CAP_PX = 188

/**
 * Evidence list capped to a whole number of rows when at least 2 rows would be hidden below the fold.
 * All `<li>` elements stay mounted in the DOM; `Expand all (N)` / `Collapse` toggles the CSS max-height cap.
 */
function CappedScroll({
  children,
  label,
  lang,
  totalCount,
}: {
  children: React.ReactNode
  label: string
  lang: Lang
  totalCount: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [expanded, setExpanded] = useState(false)
  const [view, setView] = useState<{ cap: number | null; top: boolean; below: number }>({ cap: null, top: false, below: 0 })
  const measure = useCallback(() => {
    const el = ref.current
    const list = el?.firstElementChild as HTMLElement | null
    if (!el || !list) return
    const items = Array.from(list.children) as HTMLElement[]
    const base = list.offsetTop
    const bottoms = items.map((li) => li.offsetTop - base + li.offsetHeight)
    const total = bottoms[bottoms.length - 1] ?? 0
    // Largest whole-row height that fits the cap (at least one row); only cap when >= 2 rows would be hidden.
    const rawCap = total <= CAP_PX ? null : Math.max(bottoms[0] ?? CAP_PX, ...bottoms.filter((b) => b <= CAP_PX))
    const hiddenAtTop = rawCap === null ? 0 : items.filter((li) => li.offsetTop - base + li.offsetHeight > rawCap + 1).length
    const cap = hiddenAtTop >= 2 ? rawCap : null
    const viewBottom = el.scrollTop + (cap ?? total)
    const below = cap === null ? 0 : items.filter((li) => li.offsetTop - base + li.offsetHeight > viewBottom + 1).length
    const top = !expanded && cap !== null && el.scrollTop > 2
    setView((p) => (p.cap === cap && p.top === top && p.below === below ? p : { cap, top, below }))
  }, [expanded])
  useEffect(() => {
    const el = ref.current
    if (!el) return
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    if (el.firstElementChild) ro.observe(el.firstElementChild)
    return () => ro.disconnect()
  }, [measure, totalCount])
  const scrollable = !expanded && view.cap !== null
  const mask = scrollable && view.top ? 'linear-gradient(to bottom, transparent, black 1.25rem)' : undefined
  return (
    <div className="relative flex flex-col border-t pt-1.5">
      <div
        ref={ref}
        onScroll={measure}
        tabIndex={scrollable ? 0 : undefined}
        role={scrollable ? 'region' : undefined}
        aria-label={scrollable ? label : undefined}
        data-evidence-scroll
        className={cn(
          'overscroll-contain rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring/50',
          scrollable ? 'overflow-y-auto' : 'overflow-visible',
        )}
        style={{ maxHeight: scrollable ? (view.cap ?? undefined) : undefined, maskImage: mask, WebkitMaskImage: mask }}
      >
        {children}
      </div>
      {view.cap !== null ? (
        <Button
          variant="ghost"
          aria-expanded={expanded}
          className="min-h-10 min-w-10 self-center text-xs text-muted-foreground"
          data-evidence-expand={expanded ? 'open' : 'capped'}
          data-evidence-more={!expanded && view.below > 0 ? view.below : undefined}
          onClick={() => {
            setExpanded((prev) => {
              const next = !prev
              if (!next && ref.current) ref.current.scrollTop = 0
              return next
            })
          }}
        >
          <ChevronDownIcon data-icon="inline-start" className={cn('transition-transform', expanded && 'rotate-180')} />
          {expanded ? t(lang, 'collapse') : t(lang, 'expand_all_n', { n: fmtCount(totalCount, lang) })}
        </Button>
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
  const { tu } = useUi()
  // French, Finnish and German labels are longer: they need more card width before the text shows.
  const wide = lang === 'en' ? 'hidden truncate @[21rem]:inline' : 'hidden truncate @[27rem]:inline'
  const treeText = active ? (lang === 'en' ? 'hidden truncate @[13rem]:inline' : 'hidden truncate @[18rem]:inline') : wide
  return (
    // One row always: below the threshold the two text buttons drop to icons (names stay in aria-label/title).
    <div className="@container flex min-w-0 flex-nowrap items-center gap-1">
      {!active ? (
        <Button
          variant="outline"
          className="min-w-10 shrink px-2.5"
          aria-label={t(lang, 'show_on_plan')}
          title={t(lang, 'show_on_plan')}
          onClick={() => actions().setActive(turn.id)}
        >
          <EyeIcon data-icon="inline-start" />
          <span className={wide}>{t(lang, 'show_on_plan')}</span>
        </Button>
      ) : null}
      <Button
        variant="outline"
        className="min-w-10 shrink px-2.5"
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
        <span className={treeText}>{t(lang, 'reasoning_tree')}</span>
      </Button>
      <span className="flex-1" />
      {!turn.feedback ? (
        <span className="flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            aria-label={tu('fb_up')}
            title={tu('fb_up')}
            onClick={() => actions().setFeedback(turn.id, 'up')}
          >
            <CheckIcon />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label={tu('fb_down')}
            title={tu('fb_down')}
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
  const { tu } = useUi()
  return (
    <article
      data-turn={turnId}
      data-active={active || undefined}
      aria-label={tu('answer_region')}
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

/** Printed text or prose AnswerNote: body size, quoted, clamped to 4 lines with expand/collapse toggle. */
function TextValue({
  value,
  label,
  lang,
  expandKey = 'show_all',
}: {
  value: string
  label?: string
  lang: Lang
  expandKey?: Extract<StringKey, 'show_all' | 'show_more'>
}) {
  const [all, setAll] = useState(false)
  const long = value.length > 160
  return (
    <figure className="flex flex-col gap-1" data-answer-value={value}>
      {label ? <figcaption className="text-xs font-medium text-muted-foreground">{label}</figcaption> : null}
      <blockquote
        className={cn(
          'border-l-2 border-border pl-3 text-lg leading-snug font-medium text-pretty [overflow-wrap:anywhere]',
          long && !all && 'line-clamp-4',
        )}
      >
        {value}
      </blockquote>
      {long ? (
        <Button variant="link" className="h-10 min-w-10 self-start px-0" onClick={() => setAll((v) => !v)} aria-expanded={all}>
          {t(lang, all ? 'show_less' : expandKey)}
        </Button>
      ) : null}
    </figure>
  )
}

function ClampedProse({
  text,
  lang,
  kind,
}: {
  text: string
  lang: Lang
  kind: 'explanation' | 'note'
}) {
  const [open, setOpen] = useState(false)
  const long = text.length > 220
  return (
    <div className="flex flex-col gap-0.5">
      <p
        className={cn('text-sm text-pretty text-foreground [overflow-wrap:anywhere]', long && !open && 'line-clamp-4')}
        data-explanation={kind === 'explanation' ? true : undefined}
        data-note-prose={kind === 'note' ? true : undefined}
      >
        {text}
      </p>
      {long ? (
        <Button variant="link" className="h-10 min-w-10 self-start px-0 text-xs" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {t(lang, open ? 'show_less' : 'show_more')}
        </Button>
      ) : null}
    </div>
  )
}

/** Memoised on the turn object: patchAgent keeps other turns referentially stable, so a streaming turn re-renders alone. */
export const AgentCard = memo(function AgentCard({ turn, lang: userLang }: { turn: AgentTurn; lang: Lang }) {
  const active = useAppStore((s) => s.ui.activeTurnId === turn.id)
  const derived = useDerived(turn.id)
  const drawing = useAppStore(selectDrawing)
  const activeProject = useAppStore(selectActiveProject)
  const floorLabels = useMemo(() => {
    const m = new Map<string, string>()
    for (const f of activeProject?.floors ?? []) m.set(f.drawing_id, f.label)
    return m
  }, [activeProject])
  const resp = turn.response
  const groups = useMemo(() => (resp ? evidenceGroupsOf(resp) : []), [resp])
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

  // The user's own Stop, or a spent model budget, is not a failure: a quiet, neutral card.
  if (turn.status === 'error' && (turn.error?.code === 'cancelled' || turn.error?.code === 'budget_exhausted'))
    return (
      <CardShell active={false} turnId={turn.id} lang={userLang}>
        <div className="flex min-w-0 items-start gap-2 text-muted-foreground" data-error-code={turn.error.code}>
          {turn.error.code === 'cancelled' ? (
            <SquareIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
          ) : (
            <CircleAlertIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
          )}
          {turn.error.code === 'cancelled' ? (
            <div className="flex min-w-0 flex-col gap-0.5">
              <strong className="text-sm font-semibold text-foreground">{t(userLang, 'stopped_title')}</strong>
              <p className="min-w-0 text-sm [overflow-wrap:anywhere]">{turn.error.message}</p>
            </div>
          ) : (
            <p className="min-w-0 text-sm [overflow-wrap:anywhere]">{t(userLang, 'budget_out')}</p>
          )}
        </div>
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
  const failure = agentFailure(resp)
  if (failure) return <AgentFailureCard turn={turn} failure={failure} active={active} lang={lang} />
  if (a.kind === 'abstain') {
    const title =
      a.reason === 'not_on_drawing'
        ? t(lang, 'not_on_drawing')
        : a.reason === 'not_answerable'
          ? t(lang, 'na_title')
          : a.reason === 'low_confidence'
          ? t(lang, 'low_conf_title')
          : a.reason === 'not_supported_yet'
          ? t(lang, 'not_supported_yet')
          : t(lang, 'unclear_question')
    const body =
      a.reason === 'unclear_question'
        ? t(lang, 'unclear_hint')
        : a.reason === 'not_answerable'
          ? t(lang, 'na_body')
          : a.reason === 'low_confidence'
          ? t(lang, 'low_conf_body')
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
          {a.reason === 'not_answerable' && triedText(resp) ? (
            <p className="text-xs text-muted-foreground [overflow-wrap:anywhere]" data-abstain-tried>
              {t(lang, 'na_tried', { text: triedText(resp) ?? '' })}
            </p>
          ) : null}
          {a.reason === 'low_confidence' && resp.estimate?.probabilities ? (
            <ul className="num flex flex-col gap-1 text-xs" data-estimate-probabilities>
              {Object.entries(resp.estimate.probabilities)
                .sort((x, y) => y[1] - x[1])
                .slice(0, 5)
                .map(([k, v]) => (
                  <li key={k} className="grid grid-cols-[3.5rem_1fr_2.5rem] items-center gap-2">
                    <span className="truncate">{k}</span>
                    <span className="h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
                      <span className="block h-full rounded-full bg-muted-foreground" style={{ width: `${Math.floor(v * 100)}%` }} />
                    </span>
                    <span className="text-right">{Math.floor(v * 100)} %</span>
                  </li>
                ))}
            </ul>
          ) : null}
        </div>
        <Actions turn={turn} active={active} lang={lang} />
        <Feedback turn={turn} lang={lang} />
        <Footer parts={footerParts(resp, lang)} />
      </CardShell>
    )
  }

  const parts = valueParts(resp, derived, lang)
  const chipList = chips(resp, derived, lang)
  const isProseNote = resp.answer.kind === 'note' && resp.answer.number === undefined
  const numericNoteProse =
    resp.answer.kind === 'note' &&
    resp.answer.number !== undefined &&
    resp.answer.value.trim().length > 0 &&
    resp.answer.value.trim() !== `${parts?.[0] ?? ''} ${parts?.[1] ?? ''}`.trim() &&
    resp.answer.value.trim() !== resp.explanation?.trim()
      ? resp.answer.value.trim()
      : null

  return (
    <CardShell active={active} turnId={turn.id} lang={lang}>
      {groups.length ? (
        <GroupLegend groups={groups} turn={turn} active={active} drawingId={drawing.id} floorLabels={floorLabels} />
      ) : null}
      <div className="flex flex-col gap-1.5">
        {parts && resp.answer.kind === 'text' ? (
          <TextValue value={parts[0]} label={parts[1]} lang={lang} expandKey="show_all" />
        ) : parts && isProseNote ? (
          <TextValue value={parts[0]} lang={lang} expandKey="show_more" />
        ) : parts ? (
          <p className="flex flex-wrap items-baseline gap-x-2" data-answer-value={parts[0]}>
            <span className="text-[44px] leading-none font-semibold tracking-tight tabular-nums [overflow-wrap:anywhere]">
              {parts[0]}
            </span>
            {parts[1] ? <span className="text-lg font-medium">{parts[1]}</span> : null}
            <ListenButton turnId={turn.id} response={resp} active={active} edited={derived?.changed} className="ml-auto self-center" />
          </p>
        ) : null}
        {parts && derived && metricText(resp, derived, lang) ? (
          <p className="text-sm font-medium tabular-nums text-muted-foreground" data-answer-metric>
            = {metricText(resp, derived, lang)}
          </p>
        ) : null}
        {numericNoteProse ? <ClampedProse text={numericNoteProse} lang={lang} kind="note" /> : null}
        {sentence(resp, derived, drawing, lang) ? (
          <p className="text-sm text-muted-foreground">{sentence(resp, derived, drawing, lang)}</p>
        ) : null}
        {!parts || resp.answer.kind === 'text' || isProseNote ? (
          <ListenButton turnId={turn.id} response={resp} active={active} edited={derived?.changed} className="-ml-2 self-start" />
        ) : null}
        {resp.estimate ? (
          <p className="text-sm text-muted-foreground" data-estimate>
            {resp.method === 'agent_estimate' ? (
              t(lang, 'self_confidence', { pct: Math.round(resp.estimate.confidence * 100) })
            ) : (
              <>
                {t(lang, 'estimate_sure', { pct: Math.floor(resp.estimate.confidence * 100) })}
                {resp.estimate.measured
                  ? ` ${t(lang, 'estimate_measured', { correct: resp.estimate.measured.correct, n: resp.estimate.measured.n })}`
                  : ''}
              </>
            )}
          </p>
        ) : null}
        {resp.dropped_items ? (
          <p className="text-sm text-warning" data-dropped-items={resp.dropped_items}>
            {t(lang, 'dropped_n', { n: fmtCount(resp.dropped_items, lang) })}
          </p>
        ) : null}
        {derived.noArea.length ? (
          <p className="text-sm text-warning" data-no-area={derived.noArea.length}>
            {derived.noArea.length === 1
              ? t(lang, 'no_area_1')
              : t(lang, 'no_area_n', { n: fmtCount(derived.noArea.length, lang) })}
          </p>
        ) : null}
        {resp.evidence.rows?.length ? (
          <p className="text-sm text-muted-foreground" data-evidence-rows={resp.evidence.rows.length}>
            {t(lang, 'rows_n', { n: fmtCount(resp.evidence.rows.length, lang) })}
          </p>
        ) : null}
        {resp.explanation ? (
          // Model prose that passed the provenance filter: every number in it comes from a tool result.
          <ClampedProse text={resp.explanation} lang={lang} kind="explanation" />
        ) : null}
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
      <EvidenceList turn={turn} lang={lang} active={active} floorLabels={floorLabels} />
      <Actions turn={turn} active={active} lang={lang} />
      <Feedback turn={turn} lang={lang} />
      <Footer parts={footerParts(resp, lang)} />
    </CardShell>
  )
})

const FAILURE_COPY: Record<AgentFailure, { icon: typeof CloudOffIcon; title: UiKey }> = {
  unavailable: { icon: CloudOffIcon, title: 'ag_fail_unavailable' },
  timeout: { icon: TimerOffIcon, title: 'ag_fail_timeout' },
  error: { icon: CircleAlertIcon, title: 'ag_fail_error' },
}

/** A deep-agent run that failed: neutral, translated copy, a Retry that re-asks, never the raw error text. */
function AgentFailureCard({ turn, failure, active, lang }: { turn: AgentTurn; failure: AgentFailure; active: boolean; lang: Lang }) {
  const { tu } = useUi()
  const question = useAppStore((s) => s.turns.find((x) => x.id === turn.replyTo))
  const pending = useAppStore(selectIsPending)
  const { icon: Icon, title } = FAILURE_COPY[failure]
  const retry = () => {
    if (question?.role === 'user') void actions().ask(question.text, { scope: question.scope, overrides: question.overrides })
  }
  return (
    <CardShell active={active} turnId={turn.id} lang={lang}>
      <div className="flex items-start gap-3" data-agent-failure={failure}>
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground" aria-hidden>
          <Icon className="size-4" />
        </span>
        <div className="flex min-w-0 flex-col gap-1">
          <h3 className="text-base leading-snug font-semibold text-balance">{tu(title)}</h3>
          <p className="text-sm text-muted-foreground">{tu('ag_fail_body')}</p>
          <p className="text-sm text-muted-foreground">{tu('ag_fail_hint')}</p>
        </div>
      </div>
      {question?.role === 'user' ? (
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={retry} disabled={pending} data-agent-retry>
            <RotateCcwIcon data-icon="inline-start" />
            {tu('ag_fail_retry')}
          </Button>
        </div>
      ) : null}
      <Feedback turn={turn} lang={lang} />
    </CardShell>
  )
}

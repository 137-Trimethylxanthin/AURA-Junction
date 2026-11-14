import { memo, useMemo, type KeyboardEvent, type PointerEvent } from 'react'
import { useReducedMotion } from 'motion/react'
import type { Box, EvidenceRoom, Lang, RoomPoly } from '@/api/contract'
import { isBoxRejected, isRoomIncluded, roomEvidenceId } from '@/lib/answer'
import { fmtArea } from '@/lib/format'
import { cn } from '@/lib/utils'
import { actions, useActiveTurn, useAppStore, useAppStoreShallow } from '@/store'
import { planAspect, roomTitle, subjectLabel } from './shared'
import { doubleLeaves, placeBadges } from './badges'

const hoverIn = (ids: string[]) => (e: PointerEvent) => {
  if (e.pointerType === 'mouse') actions().hover(ids)
}
const hoverOut = (e: PointerEvent) => {
  if (e.pointerType === 'mouse') actions().hover([])
}

/** Evidence of the active turn: numbered boxes for counts, lit room polygons for areas. */
export function EvidenceLayer({ stageW }: { stageW: number }) {
  const turn = useActiveTurn()
  const drawing = useAppStore((s) => s.session.drawing)
  const lang = useAppStore((s) => s.session.lang)
  const tool = useAppStore((s) => s.ui.tool)
  const { selected, hovered } = useAppStoreShallow((s) => ({
    selected: s.ui.selectedEvidenceId,
    hovered: s.ui.hoveredEvidenceIds,
  }))

  // While a streamed answer arrives, the evidence of each finished tool call lights up already (not yet toggleable).
  const streaming = turn?.status === 'pending' && turn.stream ? turn.stream : undefined
  const resp = turn?.status === 'done' ? turn.response : streaming ? { evidence: streaming.evidence } : undefined
  if (!turn || !resp) return null
  const interactive = tool === 'select' && !streaming
  return (
    <>
      {resp.evidence.rooms.length > 0 ? (
        <RoomLayer
          key={`rooms-${turn.id}`}
          turnId={turn.id}
          rooms={resp.evidence.rooms}
          polys={drawing.rooms}
          included={turn.toggles.included}
          selected={selected}
          hovered={hovered}
          lang={lang}
          interactive={interactive}
        />
      ) : null}
      {resp.evidence.boxes.length > 0 ? (
        <BoxLayer
          key={`boxes-${turn.id}`}
          turnId={turn.id}
          boxes={resp.evidence.boxes}
          rejected={turn.toggles.rejected}
          selected={selected}
          hovered={hovered}
          polys={drawing.rooms}
          lang={lang}
          interactive={interactive}
          stageW={stageW}
          aspect={planAspect(drawing)}
          leastCertain={(turn.response?.check ?? streaming?.check)?.least_certain_id}
        />
      ) : null}
    </>
  )
}

// ---- boxes -------------------------------------------------------------------

interface BoxLayerProps {
  turnId: string
  boxes: Box[]
  rejected: Record<string, boolean>
  selected: string | null
  hovered: string[]
  polys: RoomPoly[]
  lang: Lang
  interactive: boolean
  /** Stage width in px: badges are a fixed 24 px, so collisions depend on the zoom. */
  stageW: number
  /** Stage width / height (the drawing's image aspect). */
  aspect: number
  /** The box the Gemini check is least sure about: check it first. */
  leastCertain?: string
}

const BoxLayer = memo(function BoxLayer(p: BoxLayerProps) {
  const reduce = useReducedMotion()
  const roomById = useMemo(() => new Map(p.polys.map((r) => [r.room_id, r])), [p.polys])
  // Double doors: two boxes with the same rectangle. Their badges go outside the box on opposite sides.
  const { firstLeaf, secondLeaf } = useMemo(() => doubleLeaves(p.boxes), [p.boxes])
  const badgeAt = useMemo(
    () => placeBadges(p.boxes, p.stageW, p.stageW / p.aspect, firstLeaf, secondLeaf),
    [p.boxes, p.stageW, p.aspect, firstLeaf, secondLeaf],
  )

  const items = p.boxes.map((b, i) => {
    const rejected = isBoxRejected(b.id, { rejected: p.rejected, included: {} })
    const isSel = p.selected === b.id
    return { b, i, n: i + 1, rejected, isSel, hot: isSel || p.hovered.includes(b.id), leaf2: secondLeaf.has(b.id) }
  })
  const tap = (id: string) => (e: React.MouseEvent) => {
    e.stopPropagation()
    actions().tap(p.turnId, id)
  }
  const delay = (i: number) => (reduce ? undefined : `${Math.min(i, 24) * 32}ms`)

  return (
    <div className={cn('absolute inset-0', !p.interactive && 'pointer-events-none')} data-layer="boxes">
      {items.map(({ b, i, n, rejected, isSel, hot, leaf2 }) => {
        const room = b.room_id ? roomById.get(b.room_id) : undefined
        const what = `${subjectLabel(b.subject, p.lang)} ${n}${leaf2 ? ' (second copy in the CAD file; the dataset counts both)' : ''}`
        const where = room ? ` in ${roomTitle(room)}` : ''
        const state = rejected ? 'rejected' : 'counted'
        const least = b.id === p.leastCertain
        const next = isSel ? (rejected ? 'Press again to count it' : 'Press again to reject it') : 'Press to select'
        return (
          <button
            key={b.id}
            type="button"
            data-evidence-id={b.id}
            aria-label={`${what}${where}, ${state}${least ? ', least certain in the Gemini check' : ''}. ${next}`}
            data-least-certain={least || undefined}
            aria-pressed={isSel}
            tabIndex={p.interactive ? 0 : -1}
            onClick={tap(b.id)}
            onPointerEnter={hoverIn([b.id])}
            onPointerLeave={hoverOut}
            style={{ left: `${b.x}%`, top: `${b.y}%`, width: `${b.w}%`, height: `${b.h}%`, animationDelay: delay(i) }}
            className={cn(
              'plan-evidence-in absolute min-h-2.5 min-w-2.5 rounded-[3px] border-2 p-0 outline-none',
              'transition-[background-color,box-shadow,border-color] duration-150 ease-out',
              // 40 px hit area without growing the box.
              'before:absolute before:top-1/2 before:left-1/2 before:size-full before:min-h-11 before:min-w-11 before:-translate-1/2',
              'focus-visible:ring-3 focus-visible:ring-ring/60 focus-visible:ring-offset-1 focus-visible:ring-offset-background',
              rejected ? 'border-dashed border-warning bg-warning/10' : 'border-info bg-info/15 hover:bg-info/30',
              hot && (rejected ? 'z-[3] bg-warning/20 ring-4 ring-warning/30' : 'z-[3] bg-info/35 ring-4 ring-info/30'),
              // Least certain in the check: a dashed halo in the evidence colour, so it is found first.
              least && !rejected && 'outline-2 outline-offset-2 outline-info outline-dashed',
            )}
          >
            {isSel ? (
              <span
                aria-hidden
                className={cn(
                  'plan-pulse pointer-events-none absolute -inset-1 rounded-md border-2',
                  rejected ? 'border-warning' : 'border-info',
                )}
              />
            ) : null}
          </button>
        )
      })}
      {/* Number badges sit above every box, so both leaves of a double door stay reachable by their badge. */}
      <div aria-hidden className="pointer-events-none absolute inset-0 z-[4]">
        {items.map(({ b, i, n, rejected, hot }) => {
          const at = badgeAt.get(b.id) ?? { x: b.x, y: b.y, dx: 0, dy: 0 }
          return (
          <span
            key={b.id}
            onClick={tap(b.id)}
            onPointerEnter={hoverIn([b.id])}
            onPointerLeave={hoverOut}
            style={{
              left: `calc(${at.x}% + ${at.dx}px)`,
              top: `calc(${at.y}% + ${at.dy}px)`,
              animationDelay: delay(i),
            }}
            className="plan-evidence-in absolute size-0"
          >
            <span
              data-badge-for={b.id}
              className={cn(
                'num absolute -top-3 flex h-6 min-w-6 items-center justify-center rounded-full px-1 text-xs leading-none font-semibold',
                'shadow-[0_0_0_2px_var(--background)] transition-transform duration-150 ease-out motion-reduce:transition-none',
                p.interactive && 'pointer-events-auto cursor-pointer',
                '-left-3', // centred on the placed point
                rejected
                  ? 'border-[1.5px] border-dashed border-warning bg-background text-warning line-through decoration-2'
                  : 'bg-info text-primary-foreground',
                hot && 'scale-110',
              )}
            >
              {n}
            </span>
          </span>
          )
        })}
      </div>
    </div>
  )
})

// ---- rooms -------------------------------------------------------------------

interface RoomLayerProps {
  turnId: string
  rooms: EvidenceRoom[]
  polys: RoomPoly[]
  included: Record<string, boolean>
  selected: string | null
  hovered: string[]
  lang: Lang
  interactive: boolean
}

const ptsOf = (r: RoomPoly) => r.points.map(([x, y]) => `${x},${y}`).join(' ')
const pathOf = (r: RoomPoly) => `M${r.points.map(([x, y]) => `${x},${y}`).join('L')}Z`

/** Width of the invisible stroke that widens small rooms' hit area (px, non-scaling): ±14 px around the outline. */
const HIT_STROKE = 28

const RoomLayer = memo(function RoomLayer(p: RoomLayerProps) {
  const reduce = useReducedMotion()
  const polyById = useMemo(() => new Map(p.polys.map((r) => [r.room_id, r])), [p.polys])
  const toggles = { rejected: {}, included: p.included }

  const activate = (id: string) => {
    const a = actions()
    a.toggle(p.turnId, id)
    a.select(p.turnId, id)
  }
  const onKey = (id: string) => (e: KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      activate(id)
    }
  }

  return (
    <svg
      className="pointer-events-none absolute inset-0 size-full overflow-visible"
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      data-layer="rooms"
    >
      <defs>
        <pattern id="plan-hatch" patternUnits="userSpaceOnUse" width="1.2" height="1.2" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="1.2" className="stroke-muted-foreground/45" strokeWidth="0.22" />
        </pattern>
        <pattern id="plan-dots" patternUnits="userSpaceOnUse" width="1.6" height="1.6">
          <circle cx="0.8" cy="0.8" r="0.18" className="fill-muted-foreground/55" />
        </pattern>
      </defs>
      {/* Hit widening for small rooms: an invisible, wide stroke per room, all drawn UNDER every exact fill,
          so where a stroke overlaps a neighbouring room the room you see under the pointer still wins. */}
      {p.interactive ? (
        <g aria-hidden="true" data-layer="room-hit">
          {p.rooms.map((er) => {
            const poly = polyById.get(er.room_id)
            if (!poly) return null
            const id = roomEvidenceId(er.room_id)
            return (
              <path
                key={er.room_id}
                d={pathOf(poly)}
                data-evidence-id={id}
                data-hit=""
                fill="none"
                stroke="transparent"
                strokeWidth={HIT_STROKE}
                strokeLinejoin="round"
                vectorEffect="non-scaling-stroke"
                className="cursor-pointer"
                style={{ pointerEvents: 'stroke' }}
                onClick={(e) => {
                  e.stopPropagation()
                  activate(id)
                }}
                onPointerEnter={hoverIn([id])}
                onPointerLeave={hoverOut}
              />
            )
          })}
        </g>
      ) : null}
      {p.rooms.map((er, i) => {
        const poly = polyById.get(er.room_id)
        if (!poly) return null
        const id = roomEvidenceId(er.room_id)
        const inc = isRoomIncluded(er, toggles)
        const userChanged = inc !== er.included
        const isSel = p.selected === id
        const hot = isSel || p.hovered.includes(id)
        const label = `${roomTitle(poly)}${poly.outdoor ? ' (outdoor)' : ''}, ${fmtArea(er.area_m2, p.lang)} m², ${
          inc ? 'included' : 'left out'
        }${er.edited ? ', your what-if size' : ''}. Press to ${inc ? 'leave it out' : 'include it'}`
        return (
          <polygon
            key={er.room_id}
            points={ptsOf(poly)}
            role="button"
            tabIndex={p.interactive ? 0 : -1}
            aria-label={label}
            aria-pressed={inc}
            data-evidence-id={id}
            vectorEffect="non-scaling-stroke"
            onClick={(e) => {
              e.stopPropagation()
              activate(id)
            }}
            onKeyDown={onKey(id)}
            onPointerEnter={hoverIn([id])}
            onPointerLeave={hoverOut}
            style={{ animationDelay: reduce ? undefined : `${Math.min(i, 24) * 28}ms` }}
            className={cn(
              'plan-evidence-in outline-none [transform-box:fill-box] [transform-origin:center]',
              'transition-[fill-opacity,stroke-width] duration-150 ease-out',
              p.interactive ? 'pointer-events-auto cursor-pointer' : 'pointer-events-none',
              'focus-visible:stroke-ring focus-visible:[stroke-width:3px]',
              inc
                ? er.edited
                  ? 'fill-user stroke-user [stroke-width:2px]'
                  : 'fill-info stroke-info [stroke-width:1.5px]'
                : poly.outdoor
                  ? 'fill-[url(#plan-dots)] stroke-muted-foreground [stroke-dasharray:2_3] [stroke-width:1px]'
                  : 'fill-[url(#plan-hatch)] stroke-muted-foreground/70 [stroke-width:1px]',
              inc && (hot ? '[fill-opacity:0.38]' : '[fill-opacity:0.2]'),
              !inc && hot && 'stroke-foreground [stroke-width:2px]',
              userChanged && 'stroke-warning [stroke-dasharray:5_3] [stroke-width:2px]',
              isSel && 'plan-room-flash',
            )}
          />
        )
      })}
    </svg>
  )
})

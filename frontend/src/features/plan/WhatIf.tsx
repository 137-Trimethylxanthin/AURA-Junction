import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { ArrowRightIcon, XIcon } from 'lucide-react'
import type { RoomPoly } from '@/api/contract'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { fmtArea } from '@/lib/format'
import { cn } from '@/lib/utils'
import { actions, useAppStore, useAppStoreShallow } from '@/store'
import { fmtMetres, parseMetres, roomTitle } from './shared'

const round2 = (n: number) => Math.round(n * 100) / 100

/** Smallest wall we accept, in metres. */
export const MIN_M = 0.5

/** Largest width/depth that keeps the edited room inside the drawing. */
export function limitsOf(room: RoomPoly): { maxW: number; maxH: number } {
  const r = room.rect!
  return {
    maxW: Math.floor(((100 - r.x) / r.w) * r.w_m * 100) / 100,
    maxH: Math.floor(((100 - r.y) / r.h) * r.h_m * 100) / 100,
  }
}

const clampTo = (v: number, max: number) => Math.min(max, Math.max(MIN_M, round2(v)))

/** Right-click on a handle must not open the plan menu. */
const noMenu = (e: React.MouseEvent) => {
  e.preventDefault()
  e.stopPropagation()
}

function useWhatIfRoom(): RoomPoly | undefined {
  const roomId = useAppStore((s) => s.ui.whatIf.roomId)
  const rooms = useAppStore((s) => s.session.drawing.rooms)
  return roomId ? rooms.find((r) => r.room_id === roomId) : undefined
}

/** While the What-if tool is on: every room is a target; rooms with `rect` can be picked. */
export function WhatIfPicker() {
  const rooms = useAppStore((s) => s.session.drawing.rooms)
  const current = useAppStore((s) => s.ui.whatIf.roomId)
  return (
    <svg
      className="pointer-events-none absolute inset-0 z-10 size-full"
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      data-layer="whatif-picker"
    >
      {rooms.map((r) => {
        const ok = !!r.rect
        const on = r.room_id === current
        return (
          <polygon
            key={r.room_id}
            points={r.points.map(([x, y]) => `${x},${y}`).join(' ')}
            vectorEffect="non-scaling-stroke"
            role={ok ? 'button' : undefined}
            tabIndex={ok ? 0 : undefined}
            aria-label={ok ? `Change sizes of ${roomTitle(r)}` : undefined}
            aria-pressed={ok ? on : undefined}
            aria-hidden={ok ? undefined : true}
            onClick={(e) => {
              e.stopPropagation()
              if (ok) actions().startWhatIf(r.room_id)
            }}
            onKeyDown={(e) => {
              if (ok && (e.key === 'Enter' || e.key === ' ')) {
                e.preventDefault()
                actions().startWhatIf(r.room_id)
              }
            }}
            className={cn(
              'pointer-events-auto outline-none transition-[fill-opacity] duration-150 ease-out',
              ok
                ? 'cursor-pointer fill-user stroke-user/60 [fill-opacity:0.04] [stroke-dasharray:3_3] [stroke-width:1px] hover:[fill-opacity:0.14] focus-visible:stroke-ring focus-visible:[stroke-width:3px] focus-visible:[stroke-dasharray:none]'
                : 'cursor-not-allowed fill-muted-foreground [fill-opacity:0.1]',
              on && '[fill-opacity:0]',
            )}
          />
        )
      })}
    </svg>
  )
}

/** The edited room drawn at its draft size, with width/depth labels and a bottom-edge drag handle. */
export function WhatIfBox({ stageRef }: { stageRef: React.RefObject<HTMLDivElement | null> }) {
  const room = useWhatIfRoom()
  const { w, h } = useAppStoreShallow((s) => ({ w: s.ui.whatIf.w, h: s.ui.whatIf.h }))
  const mPerPctY = useAppStore((s) => s.session.drawing.m_per_pct_y)
  const drag = useRef<{ id: number; y0: number; h0: number; px: number; raf: number; next: number } | null>(null)

  if (!room?.rect) return null
  const rect = room.rect
  const { maxH } = limitsOf(room)
  const width = rect.w * (w / rect.w_m)
  const height = rect.h * (h / rect.h_m)

  const commit = () => {
    const d = drag.current
    if (!d) return
    d.raf = 0
    actions().setWhatIfDraft(actions().ui.whatIf.w, d.next)
  }
  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    e.stopPropagation()
    if (drag.current || !stageRef.current) return
    e.currentTarget.setPointerCapture(e.pointerId)
    const px = stageRef.current.getBoundingClientRect().height
    drag.current = { id: e.pointerId, y0: e.clientY, h0: h, px, raf: 0, next: h }
  }
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d || d.id !== e.pointerId) return
    const dm = ((e.clientY - d.y0) / d.px) * 100 * mPerPctY
    d.next = clampTo(d.h0 + dm, maxH)
    if (!d.raf) d.raf = requestAnimationFrame(commit)
  }
  const onUp = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d || d.id !== e.pointerId) return
    if (d.raf) cancelAnimationFrame(d.raf)
    commit()
    drag.current = null
  }
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = { ArrowDown: 0.05, ArrowUp: -0.05, ArrowRight: 0.05, ArrowLeft: -0.05 }[e.key]
    if (step === undefined) return
    e.preventDefault()
    actions().setWhatIfDraft(w, clampTo(h + step, maxH))
  }

  return (
    <div
      className="pointer-events-none absolute z-20 rounded-[2px] border-2 border-user bg-user/10"
      style={{ left: `${rect.x}%`, top: `${rect.y}%`, width: `${width}%`, height: `${height}%` }}
      data-layer="whatif-box"
    >
      <span className="num absolute -top-6 left-1/2 -translate-x-1/2 rounded-sm bg-user px-1.5 py-0.5 text-xs font-semibold whitespace-nowrap text-primary-foreground">
        {fmtMetres(w)} m
      </span>
      <span className="num absolute top-1/2 -right-1.5 translate-x-full -translate-y-1/2 rounded-sm bg-user px-1.5 py-0.5 text-xs font-semibold whitespace-nowrap text-primary-foreground">
        {fmtMetres(h)} m
      </span>
      <div
        role="slider"
        tabIndex={0}
        aria-label={`Depth of ${roomTitle(room)}: drag the bottom edge`}
        aria-orientation="vertical"
        aria-valuemin={MIN_M}
        aria-valuemax={maxH}
        aria-valuenow={h}
        aria-valuetext={`${fmtMetres(h)} metres`}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
        onClick={(e) => e.stopPropagation()}
        onContextMenu={noMenu}
        onKeyDown={onKey}
        className="group/edge pointer-events-auto absolute -bottom-5 left-1/2 flex h-10 w-14 -translate-x-1/2 cursor-ns-resize touch-none items-center justify-center outline-none"
      >
        <span
          aria-hidden
          className="h-2.5 w-10 rounded-full border-2 border-background bg-user shadow-[0_1px_4px_color-mix(in_oklch,var(--foreground)_25%,transparent)] transition-transform duration-150 ease-out group-hover/edge:scale-110 group-focus-visible/edge:ring-3 group-focus-visible/edge:ring-ring/60 group-active/edge:scale-y-125"
        />
      </div>
    </div>
  )
}

type Corner = { v: 'top' | 'bottom'; h: 'left' | 'right' }
const GAP = 12

/**
 * Where the editor hides the least: docked beside the drawing when the stage region has room
 * (right, else left, else below), otherwise the region corner farthest from the edited room.
 */
function useEditorCorner(
  scrollRef: React.RefObject<HTMLDivElement | null>,
  stageRef: React.RefObject<HTMLDivElement | null>,
  cardRef: React.RefObject<HTMLDivElement | null>,
  room: RoomPoly | undefined,
  show: boolean,
): Corner {
  const [corner, setCorner] = useState<Corner>({ v: 'bottom', h: 'left' })
  useLayoutEffect(() => {
    const sc = scrollRef.current
    if (!show || !sc) return
    const place = () => {
      const st = stageRef.current
      if (!st) return
      const reg = sc.getBoundingClientRect()
      const pl = st.getBoundingClientRect()
      const W = (cardRef.current?.offsetWidth ?? 320) + GAP * 2
      const H = (cardRef.current?.offsetHeight ?? 340) + GAP * 2
      let next: Corner
      if (reg.right - pl.right >= W) next = { v: 'bottom', h: 'right' }
      else if (pl.left - reg.left >= W) next = { v: 'bottom', h: 'left' }
      else if (reg.bottom - pl.bottom >= H) next = { v: 'bottom', h: 'left' }
      else {
        const r = room?.rect
        const cx = r ? pl.left + ((r.x + r.w / 2) / 100) * pl.width : reg.left
        const cy = r ? pl.top + ((r.y + r.h / 2) / 100) * pl.height : reg.top
        const corners: Corner[] = [
          { v: 'bottom', h: 'left' },
          { v: 'bottom', h: 'right' },
          { v: 'top', h: 'left' },
          { v: 'top', h: 'right' },
        ]
        // Distance from the room to the nearest point of the card placed in each corner.
        const dist = (c: Corner) => {
          const x0 = c.h === 'left' ? reg.left : reg.right - W
          const y0 = c.v === 'top' ? reg.top : reg.bottom - H
          const dx = Math.max(x0 - cx, 0, cx - (x0 + W))
          const dy = Math.max(y0 - cy, 0, cy - (y0 + H))
          return Math.hypot(dx, dy)
        }
        next = corners.reduce((best, c) => (dist(c) > dist(best) + 1 ? c : best))
      }
      setCorner((cur) => (cur.v === next.v && cur.h === next.h ? cur : next))
    }
    place()
    const ro = new ResizeObserver(place)
    ro.observe(sc)
    sc.addEventListener('scroll', place, { passive: true })
    return () => {
      ro.disconnect()
      sc.removeEventListener('scroll', place)
    }
  }, [scrollRef, stageRef, cardRef, room, show])
  return corner
}

/** Editor card for the What-if tool, placed where it covers the least of the drawing. */
export function WhatIfEditor({
  scrollRef,
  stageRef,
}: {
  scrollRef: React.RefObject<HTMLDivElement | null>
  stageRef: React.RefObject<HTMLDivElement | null>
}) {
  const tool = useAppStore((s) => s.ui.tool)
  const room = useWhatIfRoom()
  const reduce = useReducedMotion()
  const show = tool === 'whatif'
  const cardRef = useRef<HTMLDivElement>(null)
  const corner = useEditorCorner(scrollRef, stageRef, cardRef, room, show)
  const dy = corner.v === 'bottom' ? 12 : -12
  return (
    <AnimatePresence>
      {show ? (
        <motion.div
          ref={cardRef}
          key="whatif-editor"
          data-corner={`${corner.v}-${corner.h}`}
          initial={reduce ? { opacity: 0 } : { opacity: 0, y: dy, scale: 0.97 }}
          animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0, scale: 1 }}
          exit={reduce ? { opacity: 0 } : { opacity: 0, y: dy / 1.5, scale: 0.98, transition: { duration: 0.15 } }}
          transition={reduce ? { duration: 0.15 } : { type: 'spring', duration: 0.35, bounce: 0.12 }}
          style={{ transformOrigin: `${corner.v} ${corner.h}`, [corner.v]: GAP, [corner.h]: GAP }}
          className="absolute z-40 w-80 max-w-[calc(100%-1.5rem)] rounded-xl border bg-card p-3 text-card-foreground shadow-[0_12px_32px_-8px_color-mix(in_oklch,var(--foreground)_22%,transparent)]"
          role="region"
          aria-label="What-if sizes"
        >
          {room?.rect ? <EditorBody key={room.room_id} room={room} /> : <EditorEmpty />}
        </motion.div>
      ) : null}
    </AnimatePresence>
  )
}

function CloseButton() {
  return (
    <Button variant="ghost" size="icon" aria-label="Close what-if sizes" onClick={() => actions().cancelWhatIf()}>
      <XIcon />
    </Button>
  )
}

function EditorEmpty() {
  return (
    <div className="flex items-start justify-between gap-2">
      <div className="flex min-w-0 flex-col gap-0.5 pt-1">
        <span className="text-sm font-semibold">What-if sizes</span>
        <span className="text-xs text-muted-foreground">
          Pick a highlighted room on the plan. Rooms that aren’t rectangles can’t be resized.
        </span>
      </div>
      <CloseButton />
    </div>
  )
}

function MetreField({
  label,
  value,
  max,
  onValue,
  onValid,
}: {
  label: string
  value: number
  max: number
  onValue: (v: number) => void
  onValid: (ok: boolean) => void
}) {
  const id = useId()
  const [text, setText] = useState(() => fmtMetres(value))
  const focused = useRef(false)
  // Follow drags and arrow keys, but never overwrite what the user is typing.
  useEffect(() => {
    if (!focused.current) setText(fmtMetres(value))
  }, [value])
  const parsed = parseMetres(text)
  const ok = parsed !== null && parsed >= MIN_M && parsed <= max
  return (
    <div className="flex min-w-0 flex-col gap-1" data-invalid={!ok || undefined}>
      <label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </label>
      <Input
        id={id}
        inputMode="decimal"
        autoComplete="off"
        className="num"
        value={text}
        aria-invalid={!ok}
        aria-describedby={`${id}-range`}
        onFocus={() => (focused.current = true)}
        onBlur={() => {
          focused.current = false
          setText(fmtMetres(value))
          onValid(true)
        }}
        onChange={(e) => {
          setText(e.target.value)
          const v = parseMetres(e.target.value)
          const good = v !== null && v >= MIN_M && v <= max
          onValid(good)
          if (good) onValue(v)
        }}
      />
      <span id={`${id}-range`} className={cn('num text-xs', ok ? 'text-muted-foreground' : 'text-destructive')}>
        {fmtMetres(MIN_M)}–{fmtMetres(max)} m
      </span>
    </div>
  )
}

function EditorBody({ room }: { room: RoomPoly }) {
  const { w, h } = useAppStoreShallow((s) => ({ w: s.ui.whatIf.w, h: s.ui.whatIf.h }))
  const lang = useAppStore((s) => s.session.lang)
  const next = round2(w * h)
  const changed = next !== round2(room.area_m2)
  const { maxW, maxH } = limitsOf(room)
  const [bad, setBad] = useState({ w: false, h: false })
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-0.5 pt-1">
          <span className="truncate text-sm font-semibold">{roomTitle(room)}</span>
          <span className="text-xs text-muted-foreground">Asks a new question; the drawing stays unchanged.</span>
        </div>
        <CloseButton />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <MetreField
          label="Wall length (m)"
          value={w}
          max={maxW}
          onValue={(v) => actions().setWhatIfDraft(v, h)}
          onValid={(ok) => setBad((b) => ({ ...b, w: !ok }))}
        />
        <MetreField
          label="Room depth (m)"
          value={h}
          max={maxH}
          onValue={(v) => actions().setWhatIfDraft(w, v)}
          onValid={(ok) => setBad((b) => ({ ...b, h: !ok }))}
        />
      </div>
      <div className="flex items-baseline justify-between gap-2 rounded-lg bg-muted px-3 py-2">
        <span className="text-xs text-muted-foreground">Area</span>
        <span className="num text-sm" aria-live="polite">
          <span className={cn('text-muted-foreground', changed && 'line-through')}>{fmtArea(room.area_m2, lang)}</span>
          <ArrowRightIcon aria-hidden className="mx-1 inline size-3.5 text-muted-foreground" />
          <strong className={cn('font-semibold', changed && 'text-user')}>{fmtArea(next, lang)} m²</strong>
        </span>
      </div>
      <Button size="lg" disabled={!(w > 0 && h > 0) || bad.w || bad.h} onClick={() => void actions().applyWhatIf()}>
        Ask the agent with these sizes
      </Button>
    </div>
  )
}

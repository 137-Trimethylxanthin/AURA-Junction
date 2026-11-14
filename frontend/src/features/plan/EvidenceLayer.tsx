import {
  memo,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
} from 'react'
import { useInRouterContext, useNavigate, type NavigateFunction } from 'react-router'
import { ArrowRightIcon, EyeIcon, EyeOffIcon } from 'lucide-react'
import type { Lang, RoomPoly } from '@/api/contract'
import { useMediaQuery } from '@/hooks/use-media-query'
import { isBoxRejected, isRoomIncluded, roomEvidenceId } from '@/lib/answer'
import { fmtArea } from '@/lib/format'
import { cn } from '@/lib/utils'
import { actions, useActiveTurn, useAppStore, useAppStoreShallow } from '@/store'
import { planAspect, roomTitle, subjectLabel } from './shared'
import {
  STAGE_COPY,
  diffDroppedBoxes,
  doubleLeaves,
  extractAgentTelemetry,
  groupVarsOf,
  placeBadges,
  readActiveProject,
  readFocusedGroupId,
  rectsIntersect,
  resolveGroupStyle,
  type AgentTelemetry,
  type EvidenceGroupLike,
  type EvidenceLinkLike,
  type GroupedBox,
  type GroupedRoom,
} from './badges'

const hoverIn = (ids: string[]) => (e: PointerEvent) => {
  if (e.pointerType === 'mouse') actions().hover(ids)
}
const hoverOut = (e: PointerEvent) => {
  if (e.pointerType === 'mouse') actions().hover([])
}

function RouterNavSink({ navRef }: { navRef: React.RefObject<NavigateFunction | null> }) {
  const nav = useNavigate()
  useLayoutEffect(() => {
    navRef.current = nav
    return () => {
      navRef.current = null
    }
  }, [nav, navRef])
  return null
}

type CandidateState = 'candidate' | 'inspecting' | 'dropped'

export interface EvidenceLayerProps {
  stageW: number
  /** Controlled dropped-candidates visibility (when lifted into `PlanStage`). */
  showDropped?: boolean
  onToggleShowDropped?: () => void
  /** Controlled focused group id fallback (when lifted into `PlanStage`). */
  focusedGroupId?: string | null
  onSelectGroup?: (groupId: string | null) => void
  /** When true, floating stage controls (`StageEvidenceControls`) are mounted by `PlanStage` outside the zoom wrapper. */
  externalControls?: boolean
  onNavigateFloor?: (drawingId: string) => void
}

/**
 * Evidence of the active turn:
 * - Spotlight `/30` SVG mask veil with pixel-accurate room & box cutouts
 * - Room polygons (`RoomLayer`) & numbered object boxes (`BoxLayer`) with `--group-1..6` categorical tinting
 * - Directional flow vectors (`LinkLayer`) for stair/circulation connections & cross-floor jumps
 * - Live streaming candidate / inspecting / dropped visualization + 1.8s post-answer drop-settle & toggleable ghosts
 */
export function EvidenceLayer({
  stageW,
  showDropped: controlledShowDropped,
  onToggleShowDropped,
  focusedGroupId: controlledFocusedGroupId,
  onSelectGroup,
  externalControls = false,
  onNavigateFloor,
}: EvidenceLayerProps) {
  const turn = useActiveTurn()
  const drawing = useAppStore((s) => s.session.drawing)
  const lang = useAppStore((s) => s.session.lang)
  const tool = useAppStore((s) => s.ui.tool)
  const storeFocusedGroupId = useAppStore(readFocusedGroupId)
  const { selected, hovered } = useAppStoreShallow((s) => ({
    selected: s.ui.selectedEvidenceId,
    hovered: s.ui.hoveredEvidenceIds,
  }))
  const reduce = useMediaQuery('(prefers-reduced-motion: reduce)')

  const inRouter = useInRouterContext()
  const navRef = useRef<NavigateFunction | null>(null)

  const turnScopeKey = `${turn?.id ?? 'none'}:${drawing.id}`
  const [localDropped, setLocalDropped] = useState<{ key: string; on: boolean }>({ key: '', on: false })
  const [localGroup, setLocalGroup] = useState<{ key: string; id: string | null }>({ key: '', id: null })

  const showDropped =
    controlledShowDropped !== undefined
      ? controlledShowDropped
      : localDropped.key === turnScopeKey
        ? localDropped.on
        : false

  const toggleShowDropped = useCallback(() => {
    if (onToggleShowDropped) {
      onToggleShowDropped()
      return
    }
    setLocalDropped((prev) => ({
      key: turnScopeKey,
      on: prev.key === turnScopeKey ? !prev.on : true,
    }))
  }, [onToggleShowDropped, turnScopeKey])

  const effectiveFocusedGroupId =
    controlledFocusedGroupId !== undefined
      ? controlledFocusedGroupId
      : storeFocusedGroupId ?? (localGroup.key === turnScopeKey ? localGroup.id : null)

  const selectGroup = useCallback(
    (nextId: string | null) => {
      if (onSelectGroup) {
        onSelectGroup(nextId)
        return
      }
      const storeSetter = (actions() as unknown as { setFocusedGroupId?: (id: string | null) => void })
        .setFocusedGroupId
      if (typeof storeSetter === 'function') {
        storeSetter(nextId)
      } else {
        setLocalGroup({ key: turnScopeKey, id: nextId })
      }
    },
    [onSelectGroup, turnScopeKey],
  )

  const navigateFloor = useCallback(
    (targetDrawingId: string) => {
      onNavigateFloor?.(targetDrawingId)
      if (targetDrawingId === drawing.id) return
      if (navRef.current) {
        void navRef.current(`/d/${encodeURIComponent(targetDrawingId)}`)
      } else {
        void actions().loadDrawing(targetDrawingId)
      }
    },
    [drawing.id, onNavigateFloor],
  )

  // While a streamed answer arrives, candidate evidence lights up in real time (not yet toggleable).
  const streaming = turn?.status === 'pending' && turn.stream ? turn.stream : undefined
  const doneResp = turn?.status === 'done' ? turn.response : undefined
  const evidence = doneResp?.evidence ?? streaming?.evidence
  const turnStream = turn?.stream

  const rawBoxes = useMemo(() => (evidence?.boxes ?? []) as GroupedBox[], [evidence])
  const rawRooms = useMemo(() => (evidence?.rooms ?? []) as GroupedRoom[], [evidence])
  const groups = useMemo(
    () => ((evidence as { groups?: EvidenceGroupLike[] } | undefined)?.groups ?? []),
    [evidence],
  )
  const rawLinks = useMemo(
    () => ((evidence as { links?: EvidenceLinkLike[] } | undefined)?.links ?? []),
    [evidence],
  )

  // Preserve 1-based badge numbering from unfiltered `resp.evidence.boxes` so floor badges match AgentCard rows.
  const orderMap = useMemo(() => {
    const m = new Map<string, number>()
    rawBoxes.forEach((b, idx) => m.set(b.id, idx + 1))
    return m
  }, [rawBoxes])

  const floorBoxes = useMemo(
    () => rawBoxes.filter((b) => !b.drawing_id || b.drawing_id === drawing.id),
    [rawBoxes, drawing.id],
  )
  const floorRooms = useMemo(
    () => rawRooms.filter((r) => !r.drawing_id || r.drawing_id === drawing.id),
    [rawRooms, drawing.id],
  )
  const floorLinks = useMemo(
    () => rawLinks.filter((l) => !l.drawing_id || l.drawing_id === drawing.id),
    [rawLinks, drawing.id],
  )

  const telemetry = useMemo(
    () => (streaming ? extractAgentTelemetry(streaming, drawing.id, lang) : null),
    [streaming, drawing.id, lang],
  )

  const droppedBoxes = useMemo(
    () =>
      doneResp && turnStream
        ? diffDroppedBoxes(
            turnStream.evidence.boxes as GroupedBox[],
            doneResp.evidence.boxes as GroupedBox[],
            drawing.id,
          )
        : [],
    [doneResp, turnStream, drawing.id],
  )

  // Track live `pending -> done` transition for the brief 1.8s ghosted `×` settle (0ms under reduced motion).
  const [settlePhase, setSettlePhase] = useState<{
    key: string
    wasPending: boolean
    active: boolean
  }>({ key: '', wasPending: false, active: false })

  if (turn && settlePhase.key !== turnScopeKey) {
    setSettlePhase({
      key: turnScopeKey,
      wasPending: turn.status === 'pending',
      active: false,
    })
  } else if (turn && turn.status === 'pending' && !settlePhase.wasPending) {
    setSettlePhase({
      key: turnScopeKey,
      wasPending: true,
      active: false,
    })
  } else if (turn && turn.status === 'done' && settlePhase.wasPending) {
    setSettlePhase({
      key: turnScopeKey,
      wasPending: false,
      active: !reduce && droppedBoxes.length > 0,
    })
  }

  useEffect(() => {
    if (!settlePhase.active) return
    const timer = window.setTimeout(() => {
      setSettlePhase((prev) => (prev.active ? { ...prev, active: false } : prev))
    }, 1800)
    return () => window.clearTimeout(timer)
  }, [settlePhase.active, settlePhase.key])

  const settling = Boolean(doneResp && settlePhase.key === turnScopeKey && settlePhase.active && !reduce)

  // Compute focused evidence ids on the current floor for DOM-aware `/30` spotlighting.
  const focusedIds = useMemo(() => {
    const set = new Set<string>()
    if (streaming) return set
    const floorBoxIds = new Set(floorBoxes.map((b) => b.id))
    const floorRoomIds = new Set(floorRooms.map((r) => roomEvidenceId(r.room_id)))
    if (selected && (floorBoxIds.has(selected) || floorRoomIds.has(selected))) {
      set.add(selected)
    }
    for (const hid of hovered) {
      if (floorBoxIds.has(hid) || floorRoomIds.has(hid)) {
        set.add(hid)
      }
    }
    if (effectiveFocusedGroupId) {
      for (const b of floorBoxes) {
        if (b.group_id === effectiveFocusedGroupId) set.add(b.id)
      }
      for (const r of floorRooms) {
        if (r.group_id === effectiveFocusedGroupId) set.add(roomEvidenceId(r.room_id))
      }
    }
    return set
  }, [streaming, floorBoxes, floorRooms, selected, hovered, effectiveFocusedGroupId])

  if (!turn || !evidence) return null

  const interactive = tool === 'select' && !streaming
  const canToggleBox = !doneResp || doneResp.method !== 'agent_estimate' || doneResp.answer.kind === 'count'
  const canToggleRoom = !doneResp || doneResp.method !== 'agent_estimate'
  const aspect = planAspect(drawing)
  const spotlightOn = !streaming && focusedIds.size > 0
  const showDroppedGhosts = Boolean(doneResp && droppedBoxes.length > 0 && (settling || showDropped))

  return (
    <>
      {inRouter ? <RouterNavSink navRef={navRef} /> : null}
      {spotlightOn ? (
        <SpotlightVeil
          boxes={floorBoxes}
          rooms={floorRooms}
          polys={drawing.rooms}
          focusedIds={focusedIds}
          stageW={stageW}
          aspect={aspect}
        />
      ) : null}
      {floorRooms.length > 0 ? (
        <RoomLayer
          key={`rooms-${turn.id}-${drawing.id}`}
          turnId={turn.id}
          rooms={floorRooms}
          polys={drawing.rooms}
          groups={groups}
          included={turn.toggles.included}
          selected={selected}
          hovered={hovered}
          focusedIds={focusedIds}
          spotlightOn={spotlightOn}
          lang={lang}
          interactive={interactive}
          canToggleRoom={canToggleRoom}
        />
      ) : null}
      {showDroppedGhosts ? <DroppedCandidatesLayer boxes={droppedBoxes} settling={settling} /> : null}
      {floorBoxes.length > 0 ? (
        <BoxLayer
          key={`boxes-${turn.id}-${drawing.id}`}
          turnId={turn.id}
          boxes={floorBoxes}
          orderMap={orderMap}
          groups={groups}
          rejected={turn.toggles.rejected}
          selected={selected}
          hovered={hovered}
          focusedIds={focusedIds}
          spotlightOn={spotlightOn}
          polys={drawing.rooms}
          lang={lang}
          interactive={interactive}
          canToggleBox={canToggleBox}
          streaming={Boolean(streaming)}
          telemetry={telemetry}
          stageW={stageW}
          aspect={aspect}
          leastCertain={(turn.response?.check ?? streaming?.check)?.least_certain_id}
        />
      ) : null}
      {floorLinks.length > 0 ? (
        <LinkLayer
          links={floorLinks}
          groups={groups}
          focusedGroupId={effectiveFocusedGroupId}
          spotlightOn={spotlightOn}
          interactive={interactive}
          stageW={stageW}
          aspect={aspect}
          lang={lang}
          onNavigateFloor={navigateFloor}
        />
      ) : null}
      {!externalControls ? (
        <StageEvidenceControls
          showDropped={showDropped}
          onToggleShowDropped={toggleShowDropped}
          focusedGroupId={effectiveFocusedGroupId}
          onSelectGroup={selectGroup}
          onNavigateFloor={navigateFloor}
        />
      ) : null}
    </>
  )
}

// ---- floating stage controls (group legend + dropped toggle) -----------------

export interface StageEvidenceControlsProps {
  showDropped: boolean
  onToggleShowDropped: () => void
  focusedGroupId: string | null
  onSelectGroup: (groupId: string | null) => void
  onNavigateFloor?: (drawingId: string) => void
}

export function StageEvidenceControls({
  showDropped,
  onToggleShowDropped,
  focusedGroupId,
  onSelectGroup,
  onNavigateFloor,
}: StageEvidenceControlsProps) {
  const turn = useActiveTurn()
  const drawingId = useAppStore((s) => s.session.drawing.id)
  const project = useAppStore(readActiveProject)
  const lang = useAppStore((s) => s.session.lang)
  const copy = STAGE_COPY[lang]

  const inRouter = useInRouterContext()
  const navRef = useRef<NavigateFunction | null>(null)

  const doneResp = turn?.status === 'done' ? turn.response : undefined
  const turnStream = turn?.stream
  const groups = useMemo(
    () => ((doneResp?.evidence as { groups?: EvidenceGroupLike[] } | undefined)?.groups ?? []),
    [doneResp],
  )

  const droppedBoxes = useMemo(
    () =>
      doneResp && turnStream
        ? diffDroppedBoxes(
            turnStream.evidence.boxes as GroupedBox[],
            doneResp.evidence.boxes as GroupedBox[],
            drawingId,
          )
        : [],
    [doneResp, turnStream, drawingId],
  )

  const hasGroupItemsOnFloor = useMemo(() => {
    const set = new Set<string>()
    if (!doneResp) return set
    for (const b of doneResp.evidence.boxes as GroupedBox[]) {
      if (b.group_id && (!b.drawing_id || b.drawing_id === drawingId)) set.add(b.group_id)
    }
    for (const r of doneResp.evidence.rooms as GroupedRoom[]) {
      if (r.group_id && (!r.drawing_id || r.drawing_id === drawingId)) set.add(r.group_id)
    }
    return set
  }, [doneResp, drawingId])

  const floorLabelById = useMemo(() => {
    const map = new Map<string, string>()
    for (const f of project?.floors ?? []) {
      map.set(f.drawing_id, f.label)
    }
    return map
  }, [project])

  const jumpFloor = useCallback(
    (targetId: string) => {
      onNavigateFloor?.(targetId)
      if (targetId === drawingId) return
      if (navRef.current) {
        void navRef.current(`/d/${encodeURIComponent(targetId)}`)
      } else {
        void actions().loadDrawing(targetId)
      }
    },
    [drawingId, onNavigateFloor],
  )

  if (!doneResp || (groups.length === 0 && droppedBoxes.length === 0)) return null

  return (
    <div
      data-stage-controls=""
      className="pointer-events-auto absolute bottom-3 left-3 z-30 flex max-w-[calc(100%-1.5rem)] flex-wrap items-center gap-1.5"
    >
      {inRouter ? <RouterNavSink navRef={navRef} /> : null}
      {groups.length > 0 ? (
        <div
          role="group"
          aria-label={copy.groupLegendAria}
          data-group-legend=""
          className="flex flex-wrap items-center gap-1 rounded-xl border bg-background/95 p-1 shadow-sm backdrop-blur-xs"
        >
          {groups.map((g, idx) => {
            const vars = groupVarsOf(g.color, idx)
            const active = focusedGroupId === g.id
            const targetFloor = g.target_drawing_id && g.target_drawing_id !== drawingId ? g.target_drawing_id : undefined
            const targetLabel = targetFloor ? (floorLabelById.get(targetFloor) ?? targetFloor) : undefined
            const onCurrentFloor = hasGroupItemsOnFloor.has(g.id)
            return (
              <div key={g.id} className="flex items-center gap-0.5">
                <button
                  type="button"
                  data-group-chip={g.id}
                  aria-pressed={active}
                  onClick={() => {
                    if (!onCurrentFloor && targetFloor) {
                      jumpFloor(targetFloor)
                      return
                    }
                    onSelectGroup(active ? null : g.id)
                  }}
                  className={cn(
                    'flex h-10 min-h-10 min-w-10 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium whitespace-nowrap transition-colors outline-none',
                    'focus-visible:ring-3 focus-visible:ring-ring/60',
                    active ? 'border-foreground/30 bg-muted text-foreground shadow-xs' : 'border-transparent text-foreground hover:bg-muted/70',
                  )}
                >
                  <span
                    aria-hidden="true"
                    className="size-2.5 shrink-0 rounded-full ring-1 ring-background"
                    style={{ backgroundColor: vars.main }}
                  />
                  <span>{g.label}</span>
                  {!onCurrentFloor && targetLabel ? (
                    <span className="inline-flex items-center gap-0.5 text-muted-foreground">
                      <ArrowRightIcon aria-hidden="true" className="size-3" />
                      <span>{targetLabel}</span>
                    </span>
                  ) : null}
                </button>
                {onCurrentFloor && targetFloor && targetLabel ? (
                  <button
                    type="button"
                    data-group-jump={targetFloor}
                    aria-label={copy.jumpToFloor(targetLabel)}
                    onClick={() => jumpFloor(targetFloor)}
                    className="flex h-10 min-h-10 min-w-10 items-center justify-center gap-1 rounded-lg px-2 text-xs font-medium text-muted-foreground transition-colors outline-none hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/60"
                  >
                    <ArrowRightIcon aria-hidden="true" className="size-3" />
                    <span>{targetLabel}</span>
                  </button>
                ) : null}
              </div>
            )
          })}
        </div>
      ) : null}

      {droppedBoxes.length > 0 ? (
        <button
          type="button"
          data-toggle-dropped=""
          aria-pressed={showDropped}
          onClick={onToggleShowDropped}
          className={cn(
            'flex h-10 min-h-10 min-w-10 items-center gap-1.5 rounded-xl border px-3 text-xs font-medium whitespace-nowrap shadow-sm backdrop-blur-xs transition-colors outline-none',
            'focus-visible:ring-3 focus-visible:ring-ring/60',
            showDropped
              ? 'border-foreground/30 bg-foreground text-background'
              : 'bg-background/95 text-muted-foreground hover:bg-muted hover:text-foreground',
          )}
        >
          {showDropped ? (
            <EyeOffIcon aria-hidden="true" className="size-3.5 shrink-0" />
          ) : (
            <EyeIcon aria-hidden="true" className="size-3.5 shrink-0" />
          )}
          <span className="num">
            {showDropped ? copy.hideFilteredOut(droppedBoxes.length) : copy.showFilteredOut(droppedBoxes.length)}
          </span>
        </button>
      ) : null}
    </div>
  )
}

// ---- spotlight veil (`/30` SVG mask with room & 10px-floored box cutouts) ----

interface SpotlightVeilProps {
  boxes: readonly GroupedBox[]
  rooms: readonly GroupedRoom[]
  polys: readonly RoomPoly[]
  focusedIds: ReadonlySet<string>
  stageW: number
  aspect: number
}

const SpotlightVeil = memo(function SpotlightVeil({
  boxes,
  rooms,
  polys,
  focusedIds,
  stageW,
  aspect,
}: SpotlightVeilProps) {
  const rawId = useId()
  const maskId = `spot-mask-${rawId.replace(/[^a-zA-Z0-9_-]/g, '')}`
  const polyById = useMemo(() => new Map(polys.map((r) => [r.room_id, r])), [polys])

  const minWPct = stageW > 0 ? (10 / stageW) * 100 : 1
  const stageH = stageW > 0 ? stageW / (aspect > 0 ? aspect : 1) : 0
  const minHPct = stageH > 0 ? (10 / stageH) * 100 : 1

  return (
    <svg
      aria-hidden="true"
      data-layer="spotlight"
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      className="pointer-events-none absolute inset-0 size-full"
    >
      <defs>
        <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width="100" height="100">
          <rect x="0" y="0" width="100" height="100" fill="white" />
          {rooms.map((er) => {
            const eid = roomEvidenceId(er.room_id)
            if (!focusedIds.has(eid)) return null
            const poly = polyById.get(er.room_id)
            if (!poly) return null
            return <polygon key={eid} points={ptsOf(poly)} fill="black" />
          })}
          {boxes.map((b) => {
            if (!focusedIds.has(b.id)) return null
            const cw = Math.max(b.w, minWPct)
            const ch = Math.max(b.h, minHPct)
            const cx = b.x - (cw - b.w) / 2
            const cy = b.y - (ch - b.h) / 2
            return <rect key={b.id} x={cx} y={cy} width={cw} height={ch} rx={0.35} fill="black" />
          })}
        </mask>
      </defs>
      <rect
        x="0"
        y="0"
        width="100"
        height="100"
        mask={`url(#${maskId})`}
        className="fill-primary/30 transition-opacity duration-150 ease-out motion-reduce:transition-none"
      />
    </svg>
  )
})

// ---- ghosted post-answer dropped candidates layer ----------------------------

const DroppedCandidatesLayer = memo(function DroppedCandidatesLayer({
  boxes,
  settling,
}: {
  boxes: readonly GroupedBox[]
  settling: boolean
}) {
  return (
    <div
      aria-hidden="true"
      data-layer="dropped-candidates"
      className="pointer-events-none absolute inset-0 z-[1]"
    >
      {boxes.map((b) => (
        <div
          key={b.id}
          data-dropped-id={b.id}
          data-agent-state="dropped"
          style={{ left: `${b.x}%`, top: `${b.y}%`, width: `${b.w}%`, height: `${b.h}%` }}
          className={cn(
            'absolute min-h-2.5 min-w-2.5 rounded-[3px] border-2 border-dashed border-muted-foreground/70 bg-muted/15 opacity-30',
            settling && 'plan-drop-settle',
          )}
        >
          <span
            aria-hidden="true"
            className="absolute -top-2.5 -right-2.5 flex size-4 items-center justify-center rounded-full bg-muted text-xs leading-none font-bold text-muted-foreground ring-1 ring-background"
          >
            ×
          </span>
        </div>
      ))}
    </div>
  )
})

// ---- boxes -------------------------------------------------------------------

interface BoxLayerProps {
  turnId: string
  boxes: GroupedBox[]
  orderMap: ReadonlyMap<string, number>
  groups: readonly EvidenceGroupLike[]
  rejected: Record<string, boolean>
  selected: string | null
  hovered: string[]
  focusedIds: ReadonlySet<string>
  spotlightOn: boolean
  polys: RoomPoly[]
  lang: Lang
  interactive: boolean
  canToggleBox: boolean
  streaming: boolean
  telemetry: AgentTelemetry | null
  /** Stage width in px: badges are a fixed 24 px, so collisions depend on the zoom. */
  stageW: number
  /** Stage width / height (the drawing's image aspect). */
  aspect: number
  /** The box the Gemini check is least sure about: check it first. */
  leastCertain?: string
}

const BoxLayer = memo(function BoxLayer(p: BoxLayerProps) {
  const reduce = useMediaQuery('(prefers-reduced-motion: reduce)')
  const roomById = useMemo(() => new Map(p.polys.map((r) => [r.room_id, r])), [p.polys])

  const activeBoxesForBadges = useMemo(
    () =>
      p.streaming && p.telemetry
        ? p.boxes.filter((b) => !p.telemetry!.droppedIds.has(b.id))
        : p.boxes,
    [p.boxes, p.streaming, p.telemetry],
  )

  // Double doors: two boxes with the same rectangle. Their badges go outside the box on opposite sides.
  const { firstLeaf, secondLeaf } = useMemo(() => doubleLeaves(activeBoxesForBadges), [activeBoxesForBadges])
  const badgeAt = useMemo(
    () => placeBadges(activeBoxesForBadges, p.stageW, p.stageW / p.aspect, firstLeaf, secondLeaf),
    [activeBoxesForBadges, p.stageW, p.aspect, firstLeaf, secondLeaf],
  )

  const items = p.boxes.map((b, i) => {
    const rejected = isBoxRejected(b.id, { rejected: p.rejected, included: {} })
    const isSel = p.selected === b.id
    const isFocused = p.focusedIds.has(b.id)
    const hot = isSel || p.hovered.includes(b.id) || isFocused
    const n = p.orderMap.get(b.id) ?? i + 1
    let agentState: CandidateState | undefined
    if (p.streaming && p.telemetry) {
      if (p.telemetry.droppedIds.has(b.id)) agentState = 'dropped'
      else if (p.telemetry.focusRect && rectsIntersect(b, p.telemetry.focusRect)) agentState = 'inspecting'
      else agentState = 'candidate'
    }
    const grp = resolveGroupStyle(b.group_id, p.groups)
    return { b, i, n, rejected, isSel, isFocused, hot, leaf2: !!b.duplicate_of, agentState, grp }
  })

  const tap = (id: string) => (e: MouseEvent) => {
    e.stopPropagation()
    if (p.canToggleBox) {
      actions().tap(p.turnId, id)
    } else {
      actions().select(p.turnId, id)
    }
  }
  const delay = (i: number) => (reduce ? undefined : `${Math.min(i, 24) * 32}ms`)

  return (
    <div className={cn('absolute inset-0', !p.interactive && 'pointer-events-none')} data-layer="boxes">
      {items.map(({ b, i, n, rejected, isSel, isFocused, hot, leaf2, agentState, grp }) => {
        const room = b.room_id ? roomById.get(b.room_id) : undefined
        const what = `${subjectLabel(b.subject, p.lang)} ${n}${leaf2 ? ' (second copy in the CAD file; the dataset counts both)' : ''}`
        const where = room ? ` in ${roomTitle(room)}` : ''
        const state = rejected ? 'rejected' : 'counted'
        const least = b.id === p.leastCertain
        const next =
          isSel && p.canToggleBox
            ? rejected
              ? 'Press again to count it'
              : 'Press again to reject it'
            : 'Press to select'
        const isDropped = agentState === 'dropped'
        const isInspecting = agentState === 'inspecting'
        const isCandidate = agentState === 'candidate'
        const dimmed = p.spotlightOn && !isFocused

        const boxStyle: CSSProperties = {
          left: `${b.x}%`,
          top: `${b.y}%`,
          width: `${b.w}%`,
          height: `${b.h}%`,
          animationDelay: delay(i),
        }
        if (!rejected && !isDropped && grp) {
          boxStyle.borderColor = grp.main
          boxStyle.backgroundColor = grp.surface
          if (hot) {
            boxStyle.boxShadow = `0 0 0 4px ${grp.surface}`
          }
        }

        return (
          <button
            key={b.id}
            type="button"
            data-evidence-id={b.id}
            data-group-id={b.group_id}
            data-agent-state={agentState}
            aria-label={`${what}${where}, ${state}${least ? ', least certain in the Gemini check' : ''}. ${next}`}
            data-least-certain={least || undefined}
            aria-pressed={isSel}
            tabIndex={p.interactive ? 0 : -1}
            onClick={tap(b.id)}
            onPointerEnter={hoverIn([b.id])}
            onPointerLeave={hoverOut}
            style={boxStyle}
            className={cn(
              'plan-evidence-in absolute min-h-2.5 min-w-2.5 rounded-[3px] border-2 p-0 outline-none',
              'transition-[background-color,box-shadow,border-color,opacity] duration-150 ease-out motion-reduce:transition-none',
              // 44 px hit area without growing the box.
              'before:absolute before:top-1/2 before:left-1/2 before:size-full before:min-h-11 before:min-w-11 before:-translate-1/2',
              'focus-visible:ring-3 focus-visible:ring-ring/60 focus-visible:ring-offset-1 focus-visible:ring-offset-background',
              isDropped
                ? 'border-dashed border-muted-foreground/70 bg-muted/15 opacity-30'
                : rejected
                  ? 'border-dashed border-warning bg-warning/10'
                  : isCandidate
                    ? 'plan-candidate-pulse border-dashed border-info bg-info/15'
                    : isInspecting
                      ? 'z-[3] border-info bg-info/30 ring-4 ring-info/35'
                      : 'border-info bg-info/15 hover:bg-info/30',
              !isDropped &&
                hot &&
                (rejected
                  ? 'z-[3] bg-warning/20 ring-4 ring-warning/30'
                  : grp
                    ? 'z-[3]'
                    : 'z-[3] bg-info/35 ring-4 ring-info/30'),
              dimmed && 'opacity-35',
              // Least certain in the check: a dashed halo in the evidence colour, so it is found first.
              least && !rejected && !isDropped && 'outline-2 outline-offset-2 outline-info outline-dashed',
            )}
          >
            {isDropped ? (
              <span
                aria-hidden="true"
                className="pointer-events-none absolute -top-2.5 -right-2.5 flex size-4 items-center justify-center rounded-full bg-muted text-xs leading-none font-bold text-muted-foreground ring-1 ring-background"
              >
                ×
              </span>
            ) : null}
            {isSel && !isDropped ? (
              <span
                aria-hidden
                style={!rejected && grp ? { borderColor: grp.main } : undefined}
                className={cn(
                  'plan-pulse pointer-events-none absolute -inset-1 rounded-md border-2',
                  rejected ? 'border-warning' : !grp && 'border-info',
                )}
              />
            ) : null}
          </button>
        )
      })}
      {/* Number badges sit above every box; focused badges elevate to z-[5] while non-focused dim once (opacity-35). */}
      {items.map(({ b, i, n, rejected, isFocused, hot, agentState, grp }) => {
        if (agentState === 'dropped') return null
        const at = badgeAt.get(b.id) ?? { x: b.x, y: b.y, dx: 0, dy: 0 }
        const dimmed = p.spotlightOn && !isFocused
        const badgeStyle: CSSProperties | undefined =
          !rejected && grp ? { backgroundColor: grp.main, color: '#ffffff' } : undefined

        return (
          <span
            key={`badge-wrap-${b.id}`}
            aria-hidden="true"
            onClick={tap(b.id)}
            onPointerEnter={hoverIn([b.id])}
            onPointerLeave={hoverOut}
            style={{
              left: `calc(${at.x}% + ${at.dx}px)`,
              top: `calc(${at.y}% + ${at.dy}px)`,
              animationDelay: delay(i),
            }}
            className={cn('plan-evidence-in pointer-events-none absolute size-0', isFocused ? 'z-[5]' : 'z-[4]')}
          >
            <span
              data-badge-for={b.id}
              style={badgeStyle}
              className={cn(
                'num absolute -top-3 -left-3 flex h-6 min-w-6 items-center justify-center rounded-full px-1 text-xs leading-none font-semibold',
                'ring-2 ring-background shadow-xs transition-[transform,opacity] duration-150 ease-out motion-reduce:transition-none',
                p.interactive && 'pointer-events-auto cursor-pointer',
                rejected
                  ? 'border-[1.5px] border-dashed border-warning bg-background text-warning line-through decoration-2'
                  : !grp && 'bg-info text-primary-foreground',
                hot && 'scale-110',
                dimmed && 'opacity-35',
              )}
            >
              {n}
            </span>
          </span>
        )
      })}
    </div>
  )
})

// ---- rooms -------------------------------------------------------------------

interface RoomLayerProps {
  turnId: string
  rooms: GroupedRoom[]
  polys: RoomPoly[]
  groups: readonly EvidenceGroupLike[]
  included: Record<string, boolean>
  selected: string | null
  hovered: string[]
  focusedIds: ReadonlySet<string>
  spotlightOn: boolean
  lang: Lang
  interactive: boolean
  canToggleRoom: boolean
}

const ptsOf = (r: RoomPoly) => r.points.map(([x, y]) => `${x},${y}`).join(' ')
const pathOf = (r: RoomPoly) => `M${r.points.map(([x, y]) => `${x},${y}`).join('L')}Z`

/** Width of the invisible stroke that widens small rooms' hit area (px, non-scaling): ±14 px around the outline. */
const HIT_STROKE = 28

const RoomLayer = memo(function RoomLayer(p: RoomLayerProps) {
  const reduce = useMediaQuery('(prefers-reduced-motion: reduce)')
  const polyById = useMemo(() => new Map(p.polys.map((r) => [r.room_id, r])), [p.polys])
  const toggles = { rejected: {}, included: p.included }

  const activate = (id: string) => {
    const a = actions()
    if (p.canToggleRoom) a.toggle(p.turnId, id)
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
        const isFocused = p.focusedIds.has(id)
        const hot = isSel || p.hovered.includes(id) || isFocused
        const dimmed = p.spotlightOn && !isFocused
        const grp = resolveGroupStyle(er.group_id, p.groups)

        const actionHint = p.canToggleRoom ? `Press to ${inc ? 'leave it out' : 'include it'}` : 'Press to select'
        const label = `${roomTitle(poly)}${poly.outdoor ? ' (outdoor)' : ''}, ${er.area_m2 === null ? 'area not printed' : `${fmtArea(er.area_m2, p.lang)} m²`}, ${
          inc ? 'included' : 'left out'
        }${er.edited ? ', your what-if size' : ''}. ${actionHint}`

        const polyStyle: CSSProperties = {
          animationDelay: reduce ? undefined : `${Math.min(i, 24) * 28}ms`,
        }
        if (inc && !er.edited && !userChanged && grp) {
          polyStyle.fill = grp.main
          polyStyle.stroke = grp.main
        }

        return (
          <polygon
            key={er.room_id}
            points={ptsOf(poly)}
            role="button"
            tabIndex={p.interactive ? 0 : -1}
            aria-label={label}
            aria-pressed={inc}
            data-evidence-id={id}
            data-group-id={er.group_id}
            vectorEffect="non-scaling-stroke"
            onClick={(e) => {
              e.stopPropagation()
              activate(id)
            }}
            onKeyDown={onKey(id)}
            onPointerEnter={hoverIn([id])}
            onPointerLeave={hoverOut}
            style={polyStyle}
            className={cn(
              'plan-evidence-in outline-none [transform-box:fill-box] [transform-origin:center]',
              'transition-[fill-opacity,stroke-width,opacity] duration-150 ease-out motion-reduce:transition-none',
              p.interactive ? 'pointer-events-auto cursor-pointer' : 'pointer-events-none',
              'focus-visible:stroke-ring focus-visible:[stroke-width:3px]',
              inc
                ? er.edited
                  ? 'fill-user stroke-user [stroke-width:2px]'
                  : grp
                    ? '[stroke-width:1.75px]'
                    : 'fill-info stroke-info [stroke-width:1.5px]'
                : poly.outdoor
                  ? 'fill-[url(#plan-dots)] stroke-muted-foreground [stroke-dasharray:2_3] [stroke-width:1px]'
                  : 'fill-[url(#plan-hatch)] stroke-muted-foreground/70 [stroke-width:1px]',
              inc && (hot ? '[fill-opacity:0.38]' : '[fill-opacity:0.2]'),
              !inc && hot && 'stroke-foreground [stroke-width:2px]',
              userChanged && 'stroke-warning [stroke-dasharray:5_3] [stroke-width:2px]',
              dimmed && 'opacity-35',
              isSel && 'plan-room-flash',
            )}
          />
        )
      })}
    </svg>
  )
})

// ---- directional links / circulation flow vectors (`<LinkLayer>`) ------------

interface LinkLayerProps {
  links: readonly EvidenceLinkLike[]
  groups: readonly EvidenceGroupLike[]
  focusedGroupId: string | null
  spotlightOn: boolean
  interactive: boolean
  stageW: number
  aspect: number
  lang: Lang
  onNavigateFloor: (drawingId: string) => void
}

const LinkLayer = memo(function LinkLayer({
  links,
  groups,
  focusedGroupId,
  spotlightOn,
  interactive,
  stageW,
  aspect,
  lang,
  onNavigateFloor,
}: LinkLayerProps) {
  const rawId = useId()
  const prefix = `link-arrow-${rawId.replace(/[^a-zA-Z0-9_-]/g, '')}`
  const W = Math.max(100, stageW)
  const H = Math.max(100, stageW > 0 ? stageW / (aspect > 0 ? aspect : 1) : 100)
  const copy = STAGE_COPY[lang]

  const toPx = (pt: [number, number]): [number, number] => [(pt[0] / 100) * W, (pt[1] / 100) * H]

  return (
    <div className="pointer-events-none absolute inset-0 z-[6]" data-layer="links">
      <svg
        aria-hidden="true"
        viewBox={`0 0 ${W} ${H}`}
        className="pointer-events-none absolute inset-0 size-full overflow-visible"
      >
        <defs>
          {links.map((l) => {
            const grp = resolveGroupStyle(l.group_id, groups)
            const color = grp ? grp.main : 'var(--info)'
            return (
              <marker
                key={l.id}
                id={`${prefix}-${l.id}`}
                viewBox="0 0 10 10"
                refX="8.5"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path d="M 0 1.2 L 9 5 L 0 8.8 z" fill={color} />
              </marker>
            )
          })}
        </defs>
        {links.map((l) => {
          const grp = resolveGroupStyle(l.group_id, groups)
          const color = grp ? grp.main : 'var(--info)'
          const rawPts: [number, number][] = [l.from_pos, ...(l.waypoints ?? []), l.to_pos]
          const pxPts = rawPts.map(toPx)
          const ptsAttr = pxPts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
          const [x0, y0] = pxPts[0]
          const dimmed = spotlightOn && Boolean(focusedGroupId && l.group_id && l.group_id !== focusedGroupId)

          return (
            <g
              key={l.id}
              data-link-id={l.id}
              data-group-id={l.group_id}
              className={cn('transition-opacity duration-150 ease-out motion-reduce:transition-none', dimmed && 'opacity-35')}
            >
              <polyline
                points={ptsAttr}
                fill="none"
                stroke="var(--background)"
                strokeWidth={5}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <polyline
                points={ptsAttr}
                fill="none"
                stroke={color}
                strokeWidth={2.5}
                strokeLinecap="round"
                strokeLinejoin="round"
                markerEnd={`url(#${prefix}-${l.id})`}
              />
              <circle cx={x0} cy={y0} r={4} fill={color} stroke="var(--background)" strokeWidth={1.5} />
            </g>
          )
        })}
      </svg>

      {links.map((l) => {
        if (!l.label && !l.target_drawing_id) return null
        const grp = resolveGroupStyle(l.group_id, groups)
        const color = grp ? grp.main : 'var(--info)'
        const dimmed = spotlightOn && Boolean(focusedGroupId && l.group_id && l.group_id !== focusedGroupId)
        const pillText = l.label ?? l.target_drawing_id ?? ''

        if (l.target_drawing_id && interactive) {
          const targetId = l.target_drawing_id
          return (
            <button
              key={`pill-${l.id}`}
              type="button"
              data-link-target={targetId}
              aria-label={copy.jumpToFloor(pillText)}
              onClick={(e) => {
                e.stopPropagation()
                onNavigateFloor(targetId)
              }}
              style={{
                left: `${l.to_pos[0]}%`,
                top: `${l.to_pos[1]}%`,
                backgroundColor: color,
                color: '#ffffff',
              }}
              className={cn(
                'pointer-events-auto absolute -translate-x-1/2 -translate-y-full cursor-pointer rounded-full px-2 py-0.5 text-xs leading-tight font-semibold whitespace-nowrap ring-2 ring-background shadow-xs outline-none',
                'before:absolute before:top-1/2 before:left-1/2 before:min-h-11 before:min-w-11 before:-translate-1/2',
                'focus-visible:ring-3 focus-visible:ring-ring/60',
                dimmed && 'opacity-35',
              )}
            >
              <span className="inline-flex items-center gap-0.5">
                {!pillText.includes('→') ? <ArrowRightIcon aria-hidden="true" className="size-3" /> : null}
                <span>{pillText}</span>
              </span>
            </button>
          )
        }

        return (
          <span
            key={`pill-${l.id}`}
            aria-hidden="true"
            data-link-label={l.id}
            style={{
              left: `${l.to_pos[0]}%`,
              top: `${l.to_pos[1]}%`,
              backgroundColor: color,
              color: '#ffffff',
            }}
            className={cn(
              'pointer-events-none absolute -translate-x-1/2 -translate-y-full rounded-full px-2 py-0.5 text-xs leading-tight font-semibold whitespace-nowrap ring-2 ring-background shadow-xs',
              dimmed && 'opacity-35',
            )}
          >
            {pillText}
          </span>
        )
      })}
    </div>
  )
})

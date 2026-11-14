import { useUi } from "@/i18n/uiLang"
import {
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { AnimatePresence, motion } from "motion/react"
import { ImageOffIcon, MousePointerClickIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { ContextMenu, ContextMenuTrigger } from "@/components/ui/context-menu"
import { myProjects } from "@/api/library"
import { useMediaQuery } from "@/hooks/use-media-query"
import { cn } from "@/lib/utils"
import { ZOOM_MAX, ZOOM_MIN, actions, selectZoom, useActiveTurn, useAppStore } from "@/store"
import { EvidenceLayer, StageEvidenceControls } from "./EvidenceLayer"
import { FloorSwitcher } from "./FloorSwitcher"
import { LiveAgentHud, LiveAgentReticle } from "./LiveAgentOverlay"
import { MarkCapture, MarksLayer } from "./MarkTool"
import { PlanContextMenu } from "./PlanContextMenu"
import {
  parseSpotHash,
  readActiveProject,
  readFocusedGroupId,
  resolveProjectForDrawing,
  writeFocusedGroupId,
} from "./badges"
import {
  STAGE_COMMAND_EVENT,
  isEditable,
  overlayOpen,
  pctPoint,
  planAspect,
  type StageCommand,
} from "./shared"
import { useEver } from "@/hooks/use-ever"

// Loaded on first use: compare and what-if are not needed for the first paint of a drawing.
const CompareLayer = lazy(() =>
  import("./CompareLayer").then((m) => ({ default: m.CompareLayer }))
)
const WhatIfBox = lazy(() =>
  import("./WhatIf").then((m) => ({ default: m.WhatIfBox }))
)
const WhatIfEditor = lazy(() =>
  import("./WhatIf").then((m) => ({ default: m.WhatIfEditor }))
)
const WhatIfPicker = lazy(() =>
  import("./WhatIf").then((m) => ({ default: m.WhatIfPicker }))
)

const PAD = 30 // px around the plan inside the scroller: room for edge badges
const HINT_KEY = "aura.plan.menuHintSeen"
const SPOT_MIN_ZOOM = 190

interface ZoomAnchor {
  /** Cursor stage fraction in `[0, 1]`. */
  fx: number
  fy: number
  /** Desired viewport offset inside `scrollRef` in px. */
  vx: number
  vy: number
}

function readHintSeen(): boolean {
  try {
    return window.localStorage.getItem(HINT_KEY) === "1"
  } catch {
    return false
  }
}

function readInitialSpot(): [number, number] | null {
  if (typeof window === "undefined") return null
  return parseSpotHash(window.location.hash)
}

/** Width of the plan at 100 %: the whole drawing fits the stage region. */
function useFitWidth(ref: React.RefObject<HTMLDivElement | null>, aspect: number): number {
  const [fit, setFit] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => {
      const w = el.clientWidth - PAD * 2
      const h = el.clientHeight - PAD * 2
      setFit(Math.max(160, Math.floor(Math.min(w, h * aspect))))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref, aspect])
  return fit
}

export function PlanStage() {
  const { tu } = useUi()
  const drawing = useAppStore((s) => s.session.drawing)
  const tool = useAppStore((s) => s.ui.tool)
  const zoom = useAppStore(selectZoom)
  const compareOn = useAppStore((s) => s.ui.compare.on)
  const compareUsed = useEver(compareOn)
  const whatIfRoom = useAppStore((s) => s.ui.whatIf.roomId !== null)
  const whatIfUsed = useEver(tool === "whatif" || whatIfRoom)
  const selected = useAppStore((s) => s.ui.selectedEvidenceId)
  const turn = useActiveTurn()
  const storeProject = useAppStore(readActiveProject)
  const storeFocusedGroupId = useAppStore(readFocusedGroupId)
  const reduce = useMediaQuery("(prefers-reduced-motion: reduce)")
  const coarse = useMediaQuery("(pointer: coarse)")

  const aspect = planAspect(drawing)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const fit = useFitWidth(scrollRef, aspect)
  const width = Math.round((fit * zoom) / 100)

  const [img, setImg] = useState<"loading" | "ok" | "error">("loading")
  const [imgKey, setImgKey] = useState(0)
  const [menuPoint, setMenuPoint] = useState<[number, number] | null>(null)
  const lastPoint = useRef<[number, number] | null>(null)
  const [hintSeen, setHintSeen] = useState(readHintSeen)
  const [spotTarget, setSpotTarget] = useState<[number, number] | null>(readInitialSpot)

  // Shared dropped-candidates toggle & focused group fallback scoped to `(turn.id, drawing.id)`.
  const turnScopeKey = `${turn?.id ?? "none"}:${drawing.id}`
  const [droppedState, setDroppedState] = useState<{ key: string; on: boolean }>({ key: "", on: false })
  const [localGroupState, setLocalGroupState] = useState<{ key: string; id: string | null }>({
    key: "",
    id: null,
  })
  const [hoveredGroupState, setHoveredGroupState] = useState<{ key: string; id: string | null }>({
    key: "",
    id: null,
  })
  const [projRev, setProjRev] = useState(0)

  useEffect(() => myProjects.subscribe(() => setProjRev((r) => r + 1)), [])

  const showDropped = droppedState.key === turnScopeKey ? droppedState.on : false
  const toggleShowDropped = useCallback(() => {
    setDroppedState((prev) => ({
      key: turnScopeKey,
      on: prev.key === turnScopeKey ? !prev.on : true,
    }))
  }, [turnScopeKey])

  // Peek: hide the evidence overlays (rooms, boxes, links, marks) to read the raw linework; evidence stays in the store.
  const [peek, setPeek] = useState(false)
  const [handPan, setHandPan] = useState(false)

  const effectiveFocusedGroupId =
    storeFocusedGroupId ?? (localGroupState.key === turnScopeKey ? localGroupState.id : null)

  const hoveredGroupId = hoveredGroupState.key === turnScopeKey ? hoveredGroupState.id : null

  const selectGroup = useCallback(
    (nextId: string | null) => {
      if (!writeFocusedGroupId(nextId)) {
        setLocalGroupState({ key: turnScopeKey, id: nextId })
      }
    },
    [turnScopeKey],
  )

  const hoverGroup = useCallback(
    (nextId: string | null) => {
      setHoveredGroupState({ key: turnScopeKey, id: nextId })
    },
    [turnScopeKey],
  )

  const hasMultiFloorProject = useMemo(() => {
    void projRev
    const resolved = resolveProjectForDrawing(storeProject, drawing.id)
    return Boolean(resolved && resolved.floors.length >= 2)
  }, [storeProject, drawing.id, projRev])

  // Keep the view centred on the same spot of the drawing when zooming via toolbar, or anchored to cursor on wheel/pinch.
  const centre = useRef<[number, number]>([0.5, 0.5])
  const fitRef = useRef<number>(fit)
  const zoomFloatRef = useRef<number>(zoom)
  const zoomAnchorRef = useRef<ZoomAnchor | null>(null)
  const appliedSpotRef = useRef<string | null>(null)

  useLayoutEffect(() => {
    fitRef.current = fit
  }, [fit])

  const scrollToStageFraction = useCallback(
    (el: HTMLDivElement, st: HTMLDivElement | null, anchor: ZoomAnchor, stageW: number) => {
      const scRect = el.getBoundingClientRect()
      const stRect = st?.getBoundingClientRect()
      let contentX: number
      let contentY: number
      if (stRect && stRect.width > 0 && stRect.height > 0) {
        const originX = stRect.left - scRect.left + el.scrollLeft
        const originY = stRect.top - scRect.top + el.scrollTop
        contentX = originX + anchor.fx * stRect.width
        contentY = originY + anchor.fy * stRect.height
      } else {
        const stageH = stageW / (aspect > 0 ? aspect : 1)
        contentX = PAD + anchor.fx * stageW
        contentY = PAD + anchor.fy * stageH
      }
      el.scrollLeft = Math.max(0, contentX - anchor.vx)
      el.scrollTop = Math.max(0, contentY - anchor.vy)
      if (el.scrollWidth > 0 && el.scrollHeight > 0) {
        centre.current = [
          (el.scrollLeft + el.clientWidth / 2) / el.scrollWidth,
          (el.scrollTop + el.clientHeight / 2) / el.scrollHeight,
        ]
      }
    },
    [aspect],
  )

  const onScroll = () => {
    const el = scrollRef.current
    if (!el || el.scrollWidth === 0 || el.scrollHeight === 0) return
    centre.current = [
      (el.scrollLeft + el.clientWidth / 2) / el.scrollWidth,
      (el.scrollTop + el.clientHeight / 2) / el.scrollHeight,
    ]
  }

  useLayoutEffect(() => {
    if (Math.abs(zoomFloatRef.current - zoom) >= 1) {
      zoomFloatRef.current = zoom
    }
  }, [zoom])

  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el || width <= 0) return
    const anchor = zoomAnchorRef.current
    if (anchor) {
      zoomAnchorRef.current = null
      scrollToStageFraction(el, stageRef.current, anchor, width)
      return
    }
    const [cx, cy] = centre.current
    el.scrollLeft = cx * el.scrollWidth - el.clientWidth / 2
    el.scrollTop = cy * el.scrollHeight - el.clientHeight / 2
  }, [width, scrollToStageFraction])

  // Cursor-centered Ctrl/Cmd + Wheel zoom & two-finger touch pinch+pan zoom up to 450%.
  useEffect(() => {
    const sc = scrollRef.current
    if (!sc) return

    const captureAnchorAtClient = (clientX: number, clientY: number): ZoomAnchor => {
      const scRect = sc.getBoundingClientRect()
      const stRect = stageRef.current?.getBoundingClientRect()
      if (stRect && stRect.width > 0 && stRect.height > 0) {
        const clampedX = Math.min(stRect.right, Math.max(stRect.left, clientX))
        const clampedY = Math.min(stRect.bottom, Math.max(stRect.top, clientY))
        const fx = Math.min(1, Math.max(0, (clampedX - stRect.left) / stRect.width))
        const fy = Math.min(1, Math.max(0, (clampedY - stRect.top) / stRect.height))
        const vx = scRect.width > 0 ? clampedX - scRect.left : sc.clientWidth / 2
        const vy = scRect.height > 0 ? clampedY - scRect.top : sc.clientHeight / 2
        return { fx, fy, vx, vy }
      }
      const vx = scRect.width > 0 ? clientX - scRect.left : sc.clientWidth / 2
      const vy = scRect.height > 0 ? clientY - scRect.top : sc.clientHeight / 2
      return { fx: centre.current[0], fy: centre.current[1], vx, vy }
    }

    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return
      e.preventDefault()
      const curZoom = actions().ui.zoom
      if (Math.abs(zoomFloatRef.current - curZoom) >= 1) {
        zoomFloatRef.current = curZoom
      }
      const scaleMode = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 100 : 1
      const dy = e.deltaY * scaleMode
      const nextFloat = Math.min(
        ZOOM_MAX,
        Math.max(ZOOM_MIN, zoomFloatRef.current * Math.exp(-dy * 0.0025)),
      )
      zoomFloatRef.current = nextFloat
      const nextZoom = Math.round(nextFloat)
      if (nextZoom === curZoom) return
      zoomAnchorRef.current = captureAnchorAtClient(e.clientX, e.clientY)
      actions().setZoom(nextZoom)
    }

    let pinchStartDist = 0
    let pinchStartZoom = ZOOM_MIN
    let pinchFx = 0.5
    let pinchFy = 0.5

    const touchDist = (t0: Touch, t1: Touch) =>
      Math.hypot(t1.clientX - t0.clientX, t1.clientY - t0.clientY)

    const beginPinch = (t0: Touch, t1: Touch) => {
      const d = touchDist(t0, t1)
      if (d <= 0) return false
      pinchStartDist = d
      const curZoom = actions().ui.zoom
      if (Math.abs(zoomFloatRef.current - curZoom) >= 1) {
        zoomFloatRef.current = curZoom
      }
      pinchStartZoom = zoomFloatRef.current
      const startMidX = (t0.clientX + t1.clientX) / 2
      const startMidY = (t0.clientY + t1.clientY) / 2
      const startAnchor = captureAnchorAtClient(startMidX, startMidY)
      pinchFx = startAnchor.fx
      pinchFy = startAnchor.fy
      return true
    }

    const onTouchStart = (e: TouchEvent) => {
      if (e.touches.length !== 2 || actions().ui.tool !== "select") return
      beginPinch(e.touches[0], e.touches[1])
    }

    const onTouchMove = (e: TouchEvent) => {
      if (e.touches.length !== 2 || actions().ui.tool !== "select") return
      if (pinchStartDist <= 0 && !beginPinch(e.touches[0], e.touches[1])) return
      e.preventDefault()
      const d = touchDist(e.touches[0], e.touches[1])
      if (d <= 0) return
      const midX = (e.touches[0].clientX + e.touches[1].clientX) / 2
      const midY = (e.touches[0].clientY + e.touches[1].clientY) / 2
      const scRect = sc.getBoundingClientRect()
      const anchor: ZoomAnchor = {
        fx: pinchFx,
        fy: pinchFy,
        vx: scRect.width > 0 ? midX - scRect.left : sc.clientWidth / 2,
        vy: scRect.height > 0 ? midY - scRect.top : sc.clientHeight / 2,
      }
      const nextFloat = Math.min(
        ZOOM_MAX,
        Math.max(ZOOM_MIN, pinchStartZoom * (d / pinchStartDist)),
      )
      zoomFloatRef.current = nextFloat
      const nextZoom = Math.round(nextFloat)
      if (nextZoom === actions().ui.zoom) {
        const curWidth = Math.round((fitRef.current * nextZoom) / 100)
        if (curWidth > 0) scrollToStageFraction(sc, stageRef.current, anchor, curWidth)
        return
      }
      zoomAnchorRef.current = anchor
      actions().setZoom(nextZoom)
    }

    const onTouchEnd = (e: TouchEvent) => {
      if (e.touches.length < 2) pinchStartDist = 0
    }

    sc.addEventListener("wheel", onWheel, { passive: false })
    sc.addEventListener("touchstart", onTouchStart, { passive: true })
    sc.addEventListener("touchmove", onTouchMove, { passive: false })
    sc.addEventListener("touchend", onTouchEnd)
    sc.addEventListener("touchcancel", onTouchEnd)
    return () => {
      sc.removeEventListener("wheel", onWheel)
      sc.removeEventListener("touchstart", onTouchStart)
      sc.removeEventListener("touchmove", onTouchMove)
      sc.removeEventListener("touchend", onTouchEnd)
      sc.removeEventListener("touchcancel", onTouchEnd)
    }
  }, [scrollToStageFraction])

  // Parse `#spot=x,y` on hashchange and pan/zoom to centre `(x%, y%)` once `fit > 0`.
  useEffect(() => {
    const onHashChange = () => {
      appliedSpotRef.current = null
      setSpotTarget(parseSpotHash(window.location.hash))
    }
    window.addEventListener("hashchange", onHashChange)
    return () => window.removeEventListener("hashchange", onHashChange)
  }, [])

  useLayoutEffect(() => {
    const sc = scrollRef.current
    if (!spotTarget || fit <= 0 || !sc) return
    const spotKey = `${drawing.id}:${spotTarget[0]},${spotTarget[1]}`
    if (appliedSpotRef.current === spotKey) return
    appliedSpotRef.current = spotKey

    const anchor: ZoomAnchor = {
      fx: spotTarget[0] / 100,
      fy: spotTarget[1] / 100,
      vx: sc.clientWidth / 2,
      vy: sc.clientHeight / 2,
    }
    const curZoom = actions().ui.zoom
    if (curZoom < SPOT_MIN_ZOOM) {
      zoomFloatRef.current = SPOT_MIN_ZOOM
      zoomAnchorRef.current = anchor
      actions().setZoom(SPOT_MIN_ZOOM)
    } else {
      const curWidth = Math.round((fit * curZoom) / 100)
      scrollToStageFraction(sc, stageRef.current, anchor, curWidth)
    }
  }, [spotTarget, fit, drawing.id, scrollToStageFraction])

  // Located evidence scrolls into view inside the stage (never the page).
  useEffect(() => {
    const sc = scrollRef.current
    if (!selected || !sc) return
    const el = sc.querySelector<Element>(
      `[data-evidence-id="${CSS.escape(selected)}"]:not([data-hit])`
    )
    if (!el) return
    const a = el.getBoundingClientRect()
    const c = sc.getBoundingClientRect()
    const m = 48
    const outX = a.left < c.left + m || a.right > c.right - m
    const outY = a.top < c.top + m || a.bottom > c.bottom - m
    if (!outX && !outY) return
    sc.scrollTo({
      left: outX
        ? sc.scrollLeft + (a.left + a.width / 2) - (c.left + c.width / 2)
        : sc.scrollLeft,
      top: outY
        ? sc.scrollTop + (a.top + a.height / 2) - (c.top + c.height / 2)
        : sc.scrollTop,
      behavior: reduce ? "auto" : "smooth",
    })
  }, [selected, reduce])

  // Stage commands from keyboard shortcuts / the palette (pan, fit, peek, dropped candidates).
  useEffect(() => {
    const onCommand = (e: Event) => {
      const cmd = (e as CustomEvent<StageCommand>).detail
      const sc = scrollRef.current
      if (!cmd) return
      if (cmd.type === "pan") {
        sc?.scrollBy({ left: cmd.dx, top: cmd.dy, behavior: reduce ? "auto" : "smooth" })
      } else if (cmd.type === "fit") {
        centre.current = [0.5, 0.5]
        actions().resetZoom()
        if (sc) {
          sc.scrollLeft = (sc.scrollWidth - sc.clientWidth) / 2
          sc.scrollTop = (sc.scrollHeight - sc.clientHeight) / 2
        }
      } else if (cmd.type === "peek") {
        setPeek((p) => !p)
      } else if (cmd.type === "dropped") {
        toggleShowDropped()
      }
    }
    window.addEventListener(STAGE_COMMAND_EVENT, onCommand)
    return () => window.removeEventListener(STAGE_COMMAND_EVENT, onCommand)
  }, [reduce, toggleShowDropped])

  // Space + drag: momentary hand-pan, even while Mark or What-If is active (a capture layer sits over the stage).
  useEffect(() => {
    const interactive = "button,a,summary,[role=button],[role=checkbox],[role=switch],[role=menuitem]"
    const onDown = (e: KeyboardEvent) => {
      if (e.code !== "Space" || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return
      if (isEditable(e.target) || overlayOpen()) return
      if (e.target instanceof Element && e.target.closest(interactive)) return
      e.preventDefault()
      setHandPan(true)
    }
    const onUp = (e: KeyboardEvent) => {
      if (e.code === "Space") setHandPan(false)
    }
    const off = () => setHandPan(false)
    window.addEventListener("keydown", onDown)
    window.addEventListener("keyup", onUp)
    window.addEventListener("blur", off)
    return () => {
      window.removeEventListener("keydown", onDown)
      window.removeEventListener("keyup", onUp)
      window.removeEventListener("blur", off)
    }
  }, [])

  const panDrag = useRef<{ x: number; y: number; l: number; t: number } | null>(null)
  const onPanDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const sc = scrollRef.current
    if (!sc) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    panDrag.current = { x: e.clientX, y: e.clientY, l: sc.scrollLeft, t: sc.scrollTop }
  }
  const onPanMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = panDrag.current
    const sc = scrollRef.current
    if (!d || !sc) return
    sc.scrollLeft = d.l - (e.clientX - d.x)
    sc.scrollTop = d.t - (e.clientY - d.y)
  }
  const onPanUp = () => {
    panDrag.current = null
  }

  // Escape peels one layer: what-if, else the selection, else focused group spotlight, else #spot target ring.
  // Never when something else already handled it (an open menu or dialog, an input, the Mark tool).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (
        e.key !== "Escape" ||
        e.defaultPrevented ||
        isEditable(e.target) ||
        overlayOpen()
      )
        return
      const s = actions()
      if (s.ui.tool === "mark") return
      if (s.ui.tool === "whatif") {
        e.preventDefault()
        s.cancelWhatIf()
        stageRef.current?.focus({ preventScroll: true })
        return
      }
      if (s.ui.selectedEvidenceId && s.ui.activeTurnId) {
        e.preventDefault()
        s.select(s.ui.activeTurnId, null)
        return
      }
      if (effectiveFocusedGroupId) {
        e.preventDefault()
        selectGroup(null)
        return
      }
      if (spotTarget) {
        e.preventDefault()
        setSpotTarget(null)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [effectiveFocusedGroupId, selectGroup, spotTarget])

  const recordPoint = useCallback(
    (e: React.PointerEvent | React.MouseEvent) => {
      if (stageRef.current)
        lastPoint.current = pctPoint(stageRef.current, e.clientX, e.clientY)
    },
    []
  )

  const dismissHint = useCallback(() => {
    setHintSeen(true)
    try {
      window.localStorage.setItem(HINT_KEY, "1")
    } catch {
      /* private mode: the hint just comes back next time */
    }
  }, [])

  // When a touch long-press opens the menu, lifting the finger emits mousedown/click on the (focusable) stage,
  // which moves focus out of the menu and would dismiss it. Ignore focus leaving the menu right after it opened.
  const openedAt = useRef(0)
  const onMenuFocusOutside = (e: Event) => {
    if (performance.now() - openedAt.current < 1500) e.preventDefault()
  }

  const onMenuOpen = (open: boolean) => {
    if (!open) return
    openedAt.current = performance.now()
    setMenuPoint(lastPoint.current)
    if (!hintSeen) dismissHint()
  }

  // Shift+F10 / the Menu key on the focused stage: open the menu at the selected evidence, else the
  // centre of the visible part of the plan. We synthesise the contextmenu event ourselves so the point
  // is predictable, and swallow the browser's own one that may follow.
  const kbOpenAt = useRef(0)
  const onStageKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return
    if (!((e.shiftKey && e.key === "F10") || e.key === "ContextMenu")) return
    e.preventDefault()
    const stage = e.currentTarget
    const sc = scrollRef.current
    const sel = actions().ui.selectedEvidenceId
    const el = sel
      ? stage.querySelector(
          `[data-evidence-id="${CSS.escape(sel)}"]:not([data-hit])`
        )
      : null
    const r = (el ?? stage).getBoundingClientRect()
    let x = r.left + r.width / 2
    let y = r.top + r.height / 2
    if (!el && sc) {
      const v = sc.getBoundingClientRect()
      x = (Math.max(r.left, v.left) + Math.min(r.right, v.right)) / 2
      y = (Math.max(r.top, v.top) + Math.min(r.bottom, v.bottom)) / 2
    }
    kbOpenAt.current = performance.now()
    stage.dispatchEvent(
      new MouseEvent("contextmenu", {
        bubbles: true,
        cancelable: true,
        clientX: x,
        clientY: y,
      })
    )
  }
  const onContextMenuCapture = (e: React.MouseEvent) => {
    if (e.nativeEvent.isTrusted && performance.now() - kbOpenAt.current < 300) {
      e.preventDefault()
      e.stopPropagation()
      return
    }
    recordPoint(e)
  }
  // After the menu closes, focus goes back to the stage (not <body>) unless an item moved it.
  const onMenuCloseAutoFocus = (e: Event) => {
    e.preventDefault()
    requestAnimationFrame(() => {
      const a = document.activeElement
      if (!a || a === document.body)
        stageRef.current?.focus({ preventScroll: true })
    })
  }

  // The hint goes away after the first menu, or after 6 s.
  useEffect(() => {
    if (hintSeen) return
    const t = window.setTimeout(dismissHint, 6000)
    return () => window.clearTimeout(t)
  }, [hintSeen, dismissHint])

  const onStageClick = (e: React.MouseEvent) => {
    if (e.target !== e.currentTarget && !(e.target instanceof HTMLImageElement))
      return
    const s = actions()
    if (s.ui.tool === "select") {
      if (s.ui.selectedEvidenceId && s.ui.activeTurnId) {
        s.select(s.ui.activeTurnId, null)
      } else if (effectiveFocusedGroupId) {
        selectGroup(null)
      } else if (spotTarget) {
        setSpotTarget(null)
      }
    }
  }

  // Only at 100 %: zoomed in, the drawing fills the frame and the pill would cover badges.
  // Once there is an answer, its boxes need the whole plan: the pill steps aside (it covered the bottom at 1366×768).
  const hasTurns = useAppStore((s) => s.turns.length > 0)
  const showHint =
    !hintSeen && !hasTurns && tool === "select" && !compareOn && img === "ok" && zoom === 100
  const touchMode = tool === "select" ? "pan-x pan-y" : "none"

  return (
    <div className="absolute inset-0 overflow-hidden">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="absolute inset-0 overflow-auto overscroll-contain"
        style={{ touchAction: touchMode }}
        data-plan-scroller
      >
        <div
          className="flex min-h-full w-max min-w-full"
          style={{ padding: PAD }}
        >
          {fit > 0 ? (
            <ContextMenu onOpenChange={onMenuOpen} modal={false}>
              <ContextMenuTrigger
                asChild
                disabled={tool !== "select"}
                onPointerDownCapture={recordPoint}
                onContextMenuCapture={onContextMenuCapture}
              >
                <div
                  ref={stageRef}
                  tabIndex={0}
                  role="region"
                  aria-label={`${drawing.title}. ${tool === "select" ? "Shift+F10 opens actions for the plan." : ""}`}
                  aria-keyshortcuts="Shift+F10"
                  onKeyDown={onStageKeyDown}
                  className={cn(
                    "plan-stage relative isolate m-auto shrink-0 rounded-sm bg-card outline-none",
                    "focus-visible:ring-3 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                    "shadow-[0_0_0_1px_var(--border),0_1px_3px_color-mix(in_oklch,var(--foreground)_6%,transparent)]",
                    tool === "whatif" && "cursor-default"
                  )}
                  style={{ width, aspectRatio: `${drawing.frame.px[0]} / ${drawing.frame.px[1]}` }}
                  onClick={onStageClick}
                  data-plan-stage
                >
                  <img
                    key={imgKey}
                    src={drawing.images.rebuilt}
                    alt={`${drawing.title}: floor plan rebuilt from the CAD geometry`}
                    draggable={false}
                    onLoad={() => setImg("ok")}
                    onError={() => setImg("error")}
                    className={cn(
                      "absolute inset-0 block size-full transition-opacity duration-200 select-none",
                      img === "ok" ? "opacity-100" : "opacity-0"
                    )}
                  />
                  {img !== "ok" ? (
                    <ImageState
                      state={img}
                      onRetry={() => (
                        setImg("loading"),
                        setImgKey((k) => k + 1)
                      )}
                    />
                  ) : null}
                  {compareUsed ? (
                    <Suspense fallback={null}>
                      <CompareLayer stageRef={stageRef} />
                    </Suspense>
                  ) : null}
                  {peek ? null : tool === "whatif" ? (
                    <Suspense
                      fallback={
                        <EvidenceLayer
                          stageW={width}
                          showDropped={showDropped}
                          onToggleShowDropped={toggleShowDropped}
                          focusedGroupId={effectiveFocusedGroupId}
                          onSelectGroup={selectGroup}
                          hoveredGroupId={hoveredGroupId}
                          onHoverGroup={hoverGroup}
                          externalControls
                        />
                      }
                    >
                      <WhatIfPicker />
                    </Suspense>
                  ) : (
                    <EvidenceLayer
                      stageW={width}
                      showDropped={showDropped}
                      onToggleShowDropped={toggleShowDropped}
                      focusedGroupId={effectiveFocusedGroupId}
                      onSelectGroup={selectGroup}
                      hoveredGroupId={hoveredGroupId}
                      onHoverGroup={hoverGroup}
                      externalControls
                    />
                  )}
                  {peek ? null : <LiveAgentReticle stageW={width} aspect={aspect} />}
                  {spotTarget ? (
                    <span
                      aria-hidden="true"
                      data-spot-ring=""
                      style={{ left: `${spotTarget[0]}%`, top: `${spotTarget[1]}%` }}
                      className="pointer-events-none absolute z-[14] size-8 -translate-1/2 rounded-full border-2 border-user bg-user/10 opacity-90 shadow-[0_0_0_2px_var(--background)] ring-4 ring-user/20"
                    >
                      <span
                        aria-hidden="true"
                        className="plan-pulse absolute -inset-1.5 rounded-full border-2 border-user"
                      />
                    </span>
                  ) : null}
                  {peek ? null : <MarksLayer />}
                  {whatIfUsed ? (
                    <Suspense fallback={null}>
                      <WhatIfBox stageRef={stageRef} />
                    </Suspense>
                  ) : null}
                  {tool === "mark" ? <MarkCapture stageRef={stageRef} /> : null}
                </div>
              </ContextMenuTrigger>
              <PlanContextMenu
                point={menuPoint}
                onCloseAutoFocus={onMenuCloseAutoFocus}
                onFocusOutside={onMenuFocusOutside}
              />
            </ContextMenu>
          ) : null}
        </div>
      </div>

      {handPan ? (
        <div
          data-hand-pan
          aria-hidden="true"
          className="absolute inset-0 z-40 cursor-grab touch-none active:cursor-grabbing"
          onPointerDown={onPanDown}
          onPointerMove={onPanMove}
          onPointerUp={onPanUp}
          onPointerCancel={onPanUp}
        />
      ) : null}
      {peek ? (
        <div
          role="status"
          data-peek-badge
          className="pointer-events-none absolute top-3 left-1/2 z-30 -translate-x-1/2 rounded-full bg-foreground px-3 py-1 text-xs font-medium text-background shadow-md"
        >
          {tu("sc_peek")}
        </div>
      ) : null}
      <FloorSwitcher />
      <LiveAgentHud offsetTop={hasMultiFloorProject} />
      <StageEvidenceControls
        showDropped={showDropped}
        onToggleShowDropped={toggleShowDropped}
        focusedGroupId={effectiveFocusedGroupId}
        onSelectGroup={selectGroup}
        onHoverGroup={hoverGroup}
      />

      <AnimatePresence>
        {showHint ? (
          <motion.div
            key="hint"
            initial={{ opacity: 0, y: reduce ? 0 : 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, transition: { duration: 0.12 } }}
            transition={{
              duration: 0.25,
              ease: [0.23, 1, 0.32, 1],
              delay: reduce ? 0 : 0.6,
            }}
            className="pointer-events-none absolute bottom-1 left-1/2 z-30 flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-foreground px-3 py-1 text-xs font-medium whitespace-nowrap text-background shadow-md"
            role="status"
          >
            <MousePointerClickIcon
              aria-hidden
              className="size-3.5 opacity-80"
            />
            {coarse
              ? tu("hint_long")
              : tu("hint_right")}
          </motion.div>
        ) : null}
      </AnimatePresence>

      {tool === "mark" ? (
        <div
          role="status"
          className="pointer-events-none absolute bottom-1 left-1/2 z-30 -translate-x-1/2 rounded-full bg-user px-3 py-1 text-xs font-medium whitespace-nowrap text-primary-foreground shadow-md"
        >
          {coarse
            ? "Drag to mark an area · two fingers pan"
            : "Drag a rectangle, or use the arrow keys and Enter · Esc cancels"}
        </div>
      ) : null}

      {compareOn && drawing.images.original_alignment === "approximate" ? (
        <div className="pointer-events-none absolute right-3 bottom-3 z-30 rounded-md border bg-background/95 px-2 py-1 text-xs text-muted-foreground shadow-sm">
          Alignment approximate
        </div>
      ) : null}

      {whatIfUsed ? (
        <Suspense fallback={null}>
          <WhatIfEditor scrollRef={scrollRef} stageRef={stageRef} />
        </Suspense>
      ) : null}
    </div>
  )
}

function ImageState({
  state,
  onRetry,
}: {
  state: "loading" | "error"
  onRetry: () => void
}) {
  if (state === "loading")
    return (
      <div
        className="absolute inset-0 grid place-items-center bg-muted/60"
        role="status"
      >
        <span className="text-sm text-muted-foreground">Loading drawing…</span>
      </div>
    )
  return (
    <div
      className="absolute inset-0 grid place-items-center bg-muted/60"
      role="alert"
    >
      <div className="flex flex-col items-center gap-3 text-center">
        <ImageOffIcon aria-hidden className="size-6 text-muted-foreground" />
        <span className="text-sm text-muted-foreground">
          The drawing couldn’t be loaded.
        </span>
        <Button variant="outline" onClick={onRetry}>
          Try again
        </Button>
      </div>
    </div>
  )
}

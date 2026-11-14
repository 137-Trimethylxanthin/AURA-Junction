import {
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react"
import { AnimatePresence, motion, useReducedMotion } from "motion/react"
import { ImageOffIcon, MousePointerClickIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { ContextMenu, ContextMenuTrigger } from "@/components/ui/context-menu"
import { useMediaQuery } from "@/hooks/use-media-query"
import { cn } from "@/lib/utils"
import { actions, selectZoom, useAppStore } from "@/store"
import { EvidenceLayer } from "./EvidenceLayer"
import { MarkCapture, MarksLayer } from "./MarkTool"
import { PlanContextMenu } from "./PlanContextMenu"
import { PLAN_ASPECT, isEditable, overlayOpen, pctPoint } from "./shared"
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

function readHintSeen(): boolean {
  try {
    return window.localStorage.getItem(HINT_KEY) === "1"
  } catch {
    return false
  }
}

/** Width of the plan at 100 %: the whole drawing fits the stage region. */
function useFitWidth(ref: React.RefObject<HTMLDivElement | null>): number {
  const [fit, setFit] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => {
      const w = el.clientWidth - PAD * 2
      const h = el.clientHeight - PAD * 2
      setFit(Math.max(160, Math.floor(Math.min(w, h * PLAN_ASPECT))))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref])
  return fit
}

export function PlanStage() {
  const drawing = useAppStore((s) => s.session.drawing)
  const tool = useAppStore((s) => s.ui.tool)
  const zoom = useAppStore(selectZoom)
  const compareOn = useAppStore((s) => s.ui.compare.on)
  const compareUsed = useEver(compareOn)
  const whatIfRoom = useAppStore((s) => s.ui.whatIf.roomId !== null)
  const whatIfUsed = useEver(tool === "whatif" || whatIfRoom)
  const selected = useAppStore((s) => s.ui.selectedEvidenceId)
  const reduce = useReducedMotion()
  const coarse = useMediaQuery("(pointer: coarse)")

  const scrollRef = useRef<HTMLDivElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const fit = useFitWidth(scrollRef)
  const width = Math.round((fit * zoom) / 100)

  const [img, setImg] = useState<"loading" | "ok" | "error">("loading")
  const [imgKey, setImgKey] = useState(0)
  const [menuPoint, setMenuPoint] = useState<[number, number] | null>(null)
  const lastPoint = useRef<[number, number] | null>(null)
  const [hintSeen, setHintSeen] = useState(readHintSeen)

  // Keep the view centred on the same spot of the drawing when zooming.
  const centre = useRef<[number, number]>([0.5, 0.5])
  const onScroll = () => {
    const el = scrollRef.current
    if (!el || el.scrollWidth === 0) return
    centre.current = [
      (el.scrollLeft + el.clientWidth / 2) / el.scrollWidth,
      (el.scrollTop + el.clientHeight / 2) / el.scrollHeight,
    ]
  }
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const [cx, cy] = centre.current
    el.scrollLeft = cx * el.scrollWidth - el.clientWidth / 2
    el.scrollTop = cy * el.scrollHeight - el.clientHeight / 2
  }, [width])

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

  // Escape peels one layer: what-if, else the selection. Never when something else already handled it
  // (an open menu or dialog, an input, the Mark tool which owns Escape while active).
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
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

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

  // The hint goes away after the first menu, or after 8 s.
  useEffect(() => {
    if (hintSeen) return
    const t = window.setTimeout(dismissHint, 8000)
    return () => window.clearTimeout(t)
  }, [hintSeen, dismissHint])

  const onStageClick = (e: React.MouseEvent) => {
    if (e.target !== e.currentTarget && !(e.target instanceof HTMLImageElement))
      return
    const s = actions()
    if (s.ui.tool === "select" && s.ui.selectedEvidenceId && s.ui.activeTurnId)
      s.select(s.ui.activeTurnId, null)
  }

  // Only at 100 %: zoomed in, the drawing fills the frame and the pill would cover badges.
  const showHint =
    !hintSeen && tool === "select" && !compareOn && img === "ok" && zoom === 100
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
                  style={{ width, aspectRatio: "1221 / 1220" }}
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
                  {tool === "whatif" ? (
                    <Suspense fallback={<EvidenceLayer stageW={width} />}>
                      <WhatIfPicker />
                    </Suspense>
                  ) : (
                    <EvidenceLayer stageW={width} />
                  )}
                  <MarksLayer />
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
              ? "Long-press a room for actions"
              : "Right-click a room for actions"}
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

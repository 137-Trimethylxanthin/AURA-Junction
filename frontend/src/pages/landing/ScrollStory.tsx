import { useRef, useState, type ReactNode } from "react"
import {
  motion,
  useMotionValueEvent,
  useScroll,
  useTransform,
  type MotionValue,
} from "motion/react"
import { Undo2Icon } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { bundledDrawing } from "@/data/drawing"
import { useMediaQuery } from "@/hooks/use-media-query"
import { cn } from "@/lib/utils"
import { BOXES, GOLD } from "./demoData"
import { factById } from "./facts"
import { PlanTile } from "./PlanTile"
import { Reveal, RevealItem } from "./motion"
import { usePrefersReducedMotion } from "./springs"

/**
 * "How it works", told on the real plan. On a laptop the plan stays pinned while the page scrolls
 * natively past four steps, and every change on the plan is tied to scroll progress:
 * rooms light up (geometry), the 12 window boxes draw in one by one while the count follows,
 * a tile frame walks across the boxes (the check), and one box is rejected (you).
 * Narrow screens and reduced motion get the same four steps as still cards.
 */

const N = BOXES.length
const REJECT = Math.min(4, N - 1) // the box the story rejects: number 5
/** Scroll-progress windows (0–1 over the pinned track). */
const GEO = [0, 0.25] as const
const DRAW = [0.27, 0.47] as const
const SCAN = [0.52, 0.74] as const
const REJECT_AT = 0.8

interface StepText {
  title: string
  body: ReactNode
  tone: "success" | "info" | "warning"
}

function useSteps(): StepText[] {
  const check = factById("check-cost-library")
  return [
    {
      tone: "success",
      title: "Reads the CAD geometry",
      body: (
        <>
          Rooms, walls, doors and windows come from the DXF layers. Counts and
          areas are plain arithmetic:{" "}
          <strong className="font-medium text-foreground">0 model calls</strong>
          .
        </>
      ),
    },
    {
      tone: "info",
      title: "Draws each item, numbered",
      body: "Every window it counted gets a numbered box on the plan, so the number on the answer card is something you can count yourself.",
    },
    {
      tone: "info",
      title: "Gemini checks each box",
      body: check ? (
        <>
          A zoomed tile around every box of one kind goes to the Gemini
          Decisions API in one request: {check.value} on average ({check.n}{" "}
          requests, 710 boxes; 708 agreed with the solver’s boxes).
        </>
      ) : (
        "A zoomed tile around every box of one kind goes to the Gemini Decisions API in one request."
      ),
    },
    {
      tone: "warning",
      title: "You keep or reject",
      body: "Reject a box and the number follows, marked as your correction next to the dataset value. Undo puts it back.",
    },
  ]
}

const DOT = { success: "bg-success", info: "bg-info", warning: "bg-warning" }

export function ScrollStory() {
  const wide = useMediaQuery("(min-width: 1024px) and (min-height: 640px)")
  const reduce = usePrefersReducedMotion()
  const steps = useSteps()
  return wide && !reduce ? (
    <Pinned steps={steps} />
  ) : (
    <StillSteps steps={steps} />
  )
}

// ---- pinned, scroll-linked ---------------------------------------------------------------------

function Pinned({ steps }: { steps: StepText[] }) {
  const track = useRef<HTMLDivElement>(null)
  const { scrollYProgress: p } = useScroll({
    target: track,
    offset: ["start start", "end end"],
  })
  const [state, setState] = useState({
    step: 0,
    count: 0,
    scan: -1,
    rejected: false,
  })

  useMotionValueEvent(p, "change", (v) => {
    const step = Math.min(3, Math.floor(v * 4))
    const count =
      v < DRAW[0]
        ? 0
        : Math.min(N, Math.floor(((v - DRAW[0]) / (DRAW[1] - DRAW[0])) * N) + 1)
    const scan =
      v < SCAN[0] || v > SCAN[1]
        ? -1
        : Math.min(N - 1, Math.floor(((v - SCAN[0]) / (SCAN[1] - SCAN[0])) * N))
    const rejected = v >= REJECT_AT
    setState((s) =>
      s.step === step &&
      s.count === count &&
      s.scan === scan &&
      s.rejected === rejected
        ? s
        : { step, count, scan, rejected }
    )
  })

  const goTo = (i: number) => {
    const el = track.current
    if (!el) return
    const span = el.offsetHeight - window.innerHeight
    const top =
      el.getBoundingClientRect().top + window.scrollY + span * ((i + 0.5) / 4)
    window.scrollTo({ top, behavior: "smooth" })
  }

  const shown = state.rejected ? state.count - 1 : state.count
  const rail = useTransform(p, [0, 1], [0, 1])

  return (
    <div
      ref={track}
      className="relative"
      style={{ height: "220vh" }}
      data-story
    >
      <div className="sticky top-16 flex h-[calc(100dvh-4rem)] items-center">
        <div className="grid w-full grid-cols-[minmax(0,19rem)_minmax(0,1fr)] items-center gap-8 xl:grid-cols-[minmax(0,21rem)_minmax(0,1fr)] xl:gap-10">
          {/* Steps: always in the DOM and in reading order; the active one is lit. */}
          <ol className="relative flex flex-col gap-2 pl-5">
            <span
              aria-hidden
              className="absolute top-2 bottom-2 left-0 w-px bg-border"
            />
            <motion.span
              aria-hidden
              className="absolute top-2 bottom-2 left-0 w-px origin-top bg-foreground"
              style={{ scaleY: rail }}
            />
            {steps.map((s, i) => {
              const active = state.step === i
              return (
                <li key={s.title}>
                  <button
                    type="button"
                    onClick={() => goTo(i)}
                    aria-current={active ? "step" : undefined}
                    className={cn(
                      "l-press flex w-full flex-col gap-1 rounded-xl px-4 py-3 text-left transition-[background-color,box-shadow,color] duration-200 outline-none focus-visible:ring-3 focus-visible:ring-ring",
                      active
                        ? "bg-card text-foreground shadow-sm ring-1 ring-foreground/15"
                        : "text-foreground/80 hover:bg-muted/60 hover:text-foreground"
                    )}
                  >
                    <span className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                      <span
                        aria-hidden
                        className={cn("size-2 rounded-full", DOT[s.tone])}
                      />
                      <span className="tabular-nums">Step {i + 1}</span>
                    </span>
                    <span
                      className={cn(
                        "text-lg font-semibold tracking-[-0.01em]",
                        active ? "text-foreground" : "text-foreground/85"
                      )}
                    >
                      {s.title}
                    </span>
                    <span className="text-sm text-pretty text-muted-foreground">
                      {s.body}
                    </span>
                  </button>
                </li>
              )
            })}
          </ol>

          {/* Stage: the real plan and the real boxes. Decorative mirror of the steps' text. */}
          <Card
            aria-hidden
            className="grid min-w-0 grid-cols-[minmax(0,1fr)_12rem] gap-0 p-0"
          >
            <div className="flex items-center justify-center border-r bg-background p-3">
              <div className="relative aspect-[1221/1220] w-full max-w-[min(100%,calc(100dvh-10rem))]">
                <img
                  src={bundledDrawing.images.rebuilt}
                  alt=""
                  width={1221}
                  height={1220}
                  loading="lazy"
                  decoding="async"
                  draggable={false}
                  className="absolute inset-0 size-full select-none"
                />
                <Rooms p={p} />
                {BOXES.map((b, i) => (
                  <StoryBox
                    key={b.id}
                    i={i}
                    p={p}
                    scanning={state.scan === i}
                    rejected={state.rejected && i === REJECT}
                  />
                ))}
              </div>
            </div>
            <SidePanel state={state} shown={shown} />
          </Card>
        </div>
      </div>
    </div>
  )
}

/** Step 1: the room polygons from the geometry light up, then settle back under the boxes. */
function Rooms({ p }: { p: MotionValue<number> }) {
  const opacity = useTransform(p, [GEO[0], 0.06, 0.2, 0.3], [0, 1, 1, 0.18])
  return (
    <motion.svg
      className="pointer-events-none absolute inset-0 size-full"
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      style={{ opacity }}
    >
      {bundledDrawing.rooms.map((r) => (
        <polygon
          key={r.room_id}
          points={r.points.map(([x, y]) => `${x},${y}`).join(" ")}
          vectorEffect="non-scaling-stroke"
          className={
            r.outdoor
              ? "fill-success/5 stroke-success/60"
              : "fill-success/12 stroke-success"
          }
          strokeWidth={1.25}
        />
      ))}
    </motion.svg>
  )
}

function StoryBox({
  i,
  p,
  scanning,
  rejected,
}: {
  i: number
  p: MotionValue<number>
  scanning: boolean
  rejected: boolean
}) {
  const b = BOXES[i]
  const step = (DRAW[1] - DRAW[0]) / N
  const from = DRAW[0] + i * step
  const opacity = useTransform(p, [from, from + step * 1.5], [0, 1])
  const scale = useTransform(p, [from, from + step * 1.5], [0.6, 1])
  return (
    <motion.span
      className="absolute"
      style={{
        left: `${b.x}%`,
        top: `${b.y}%`,
        width: `${b.w}%`,
        height: `${b.h}%`,
        opacity,
      }}
    >
      <span
        className={cn(
          "absolute inset-0 min-h-2 min-w-2 rounded-[3px] border-2 transition-colors duration-300",
          rejected
            ? "border-dashed border-warning bg-warning/10"
            : "border-info bg-info/15"
        )}
      />
      {scanning ? (
        <motion.span
          layoutId="story-scan"
          className="absolute -inset-2 rounded-md border-2 border-info shadow-[0_0_0_4px_color-mix(in_oklch,var(--info),transparent_80%)]"
          transition={{ type: "spring", bounce: 0, duration: 0.3 }}
        />
      ) : null}
      <motion.span
        style={{ scale }}
        className={cn(
          "absolute -top-3 -left-3 flex size-6 items-center justify-center rounded-full text-xs font-semibold tabular-nums shadow-[0_0_0_2px_var(--background)] transition-colors duration-300",
          rejected
            ? "border-[1.5px] border-dashed border-warning bg-background text-warning line-through"
            : "bg-info text-primary-foreground"
        )}
      >
        {i + 1}
      </motion.span>
    </motion.span>
  )
}

function SidePanel({
  state,
  shown,
}: {
  state: { step: number; count: number; scan: number; rejected: boolean }
  shown: number
}) {
  const scanBox = BOXES[Math.max(0, state.scan)]
  return (
    <div className="flex min-w-0 flex-col gap-4 p-4">
      <div className="flex flex-col gap-1">
        <div className="flex items-baseline gap-2">
          <span
            key={shown}
            className={cn(
              "l-tick text-5xl leading-none font-semibold tracking-[-0.03em] tabular-nums",
              state.rejected && "text-warning"
            )}
          >
            {state.count ? (
              shown
            ) : (
              <span className="text-muted-foreground">–</span>
            )}
          </span>
          <span className="text-base font-medium">windows</span>
        </div>
        <div className="flex flex-wrap gap-1.5 pt-1">
          <Badge className="bg-success-surface text-success">
            Exact · CAD geometry
          </Badge>
          {state.rejected && GOLD !== undefined ? (
            <Badge className="bg-warning-surface text-warning">
              Dataset: {GOLD} · you: {shown}
            </Badge>
          ) : state.count === N && GOLD !== undefined ? (
            <Badge className="bg-success-surface text-success">
              Dataset: {GOLD} ✓
            </Badge>
          ) : null}
        </div>
      </div>

      <div className="mt-auto flex flex-col gap-2 border-t pt-4 text-xs text-muted-foreground">
        {state.step === 0 ? (
          <>
            <Row k="window layer" v={`${N} items`} />
            <Row k="room polygons" v={`${bundledDrawing.rooms.length} rooms`} />
            <Row k="model calls" v="0" />
          </>
        ) : state.step === 1 ? (
          <>
            <Row k="boxes drawn" v={`${state.count} of ${N}`} />
            <Row k="model calls" v="0" />
          </>
        ) : state.step === 2 ? (
          <>
            <PlanTile
              box={scanBox}
              className="aspect-square size-auto w-full"
            />
            <Row k="tile" v={`${Math.max(0, state.scan) + 1} of ${N}`} />
            <Row k="requests" v="1" />
          </>
        ) : (
          <>
            <Row k="rejected" v={`box ${REJECT + 1}`} />
            <span className="inline-flex items-center gap-1.5">
              <Undo2Icon className="size-3.5" aria-hidden /> Undo {REJECT + 1}
            </span>
          </>
        )}
      </div>
    </div>
  )
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <span className="flex justify-between gap-2 rounded-md bg-muted px-2.5 py-1.5 font-mono">
      <span>{k}</span>
      <span className="text-foreground tabular-nums">{v}</span>
    </span>
  )
}

// ---- still cards ---------------------------------------------------------------------------------

function StillSteps({ steps }: { steps: StepText[] }) {
  const visuals: ReactNode[] = [
    <div key="g" className="flex w-full max-w-60 flex-col gap-1.5 text-xs">
      <Row k="window layer" v={`${N} items`} />
      <Row k="room polygons" v={`${bundledDrawing.rooms.length} rooms`} />
      <Badge className="bg-success-surface text-success">
        Exact · CAD geometry
      </Badge>
    </div>,
    <div key="b" className="relative h-24 w-48">
      {[
        [6, 10, 34, 14],
        [52, 6, 14, 40],
        [8, 56, 46, 14],
      ].map(([x, y, w, h], i) => (
        <span
          key={i}
          className="absolute rounded-[3px] border-2 border-info bg-info/15"
          style={{
            left: `${x}%`,
            top: `${y}%`,
            width: `${w}%`,
            height: `${h}%`,
          }}
        >
          <span className="absolute -top-3 -left-3 flex size-6 items-center justify-center rounded-full bg-info text-xs font-semibold text-primary-foreground tabular-nums shadow-[0_0_0_2px_var(--background)]">
            {i + 1}
          </span>
        </span>
      ))}
    </div>,
    <div key="t" className="flex items-center gap-3">
      <PlanTile box={BOXES[1]} />
      <Badge variant="outline" className="border-info/40 text-info">
        1 request
      </Badge>
    </div>,
    <div key="r" className="flex flex-col items-start gap-2">
      <div className="flex items-center gap-2 text-xs font-semibold tabular-nums">
        <span className="inline-flex h-6 min-w-7 items-center justify-center rounded-md bg-info text-primary-foreground">
          4
        </span>
        <span className="inline-flex h-6 min-w-7 items-center justify-center rounded-md border-[1.5px] border-dashed border-warning text-warning line-through">
          5
        </span>
        <span className="inline-flex h-6 min-w-7 items-center justify-center rounded-md bg-info text-primary-foreground">
          6
        </span>
      </div>
      {GOLD !== undefined ? (
        <Badge className="bg-warning-surface text-warning">
          Dataset: {GOLD} · you: {GOLD - 1}
        </Badge>
      ) : null}
    </div>,
  ]
  return (
    <Reveal as="ol" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {steps.map((s, i) => (
        <RevealItem as="li" key={s.title}>
          <Card className="h-full pt-0">
            <div
              aria-hidden
              className="flex h-40 items-center justify-center border-b bg-background p-4"
            >
              {visuals[i]}
            </div>
            <CardHeader>
              <p className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <span
                  aria-hidden
                  className={cn("size-2 rounded-full", DOT[s.tone])}
                />
                <span className="tabular-nums">Step {i + 1}</span>
              </p>
              <CardTitle className="font-semibold">{s.title}</CardTitle>
              <CardDescription className="text-pretty">
                {s.body}
              </CardDescription>
            </CardHeader>
          </Card>
        </RevealItem>
      ))}
    </Reveal>
  )
}

export default ScrollStory

import { useEffect, useMemo, useRef, useState } from "react"
import { RotateCcwIcon, Undo2Icon } from "lucide-react"
import { bundledDrawing } from "@/data/drawing"
import { deriveAnswer, type Toggles } from "@/lib/answer"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import {
  announce,
  chips,
  footerParts,
  rows,
  sentence,
  valueParts,
  type Chip,
} from "@/features/chat/view"
import { placeBadges } from "@/features/plan/badges"
import { BOXES, LEAVES, QUESTION, RESPONSE } from "./demoData"
import { PLAN_ASPECT } from "@/features/plan/shared"
import "@/features/plan/plan.css"

/**
 * The hero's live proof: the real local answer for swiss-46639 (same code path as the app's offline
 * adapter), its real evidence boxes on the real plan, and the real toggle arithmetic. Nothing is canned.
 */

const CHIP_TONE: Record<Chip["tone"], string> = {
  success: "bg-success-surface text-success",
  info: "bg-info-surface text-info",
  warning: "bg-warning-surface text-warning",
  destructive: "bg-destructive-surface text-destructive",
  neutral: "bg-muted text-foreground",
}

/** Entrance timing: plan first, then the boxes in reading order, then the answer. */
const BOX_START = 380
const BOX_STEP = 45

function useStageWidth() {
  const ref = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}

export function HeroDemo() {
  const [rejected, setRejected] = useState<Record<string, boolean>>({})
  const [history, setHistory] = useState<string[]>([])
  const [hot, setHot] = useState<string | null>(null)
  const [stageRef, stageW] = useStageWidth()

  const toggles: Toggles = useMemo(
    () => ({ rejected, included: {} }),
    [rejected]
  )
  const d = useMemo(
    () => deriveAnswer(RESPONSE, toggles, bundledDrawing),
    [toggles]
  )
  const value = valueParts(RESPONSE, d, "en")
  // The local adapter marks its image check as a sample (no model ran): the demo shows only measured chips.
  const chipList = chips(RESPONSE, d, "en").filter((c) => c.key !== "check")
  const list = useMemo(
    () => rows(RESPONSE, toggles, bundledDrawing, "en"),
    [toggles]
  )
  const badgeAt = useMemo(
    () =>
      stageW
        ? placeBadges(
            BOXES,
            stageW,
            stageW / PLAN_ASPECT,
            LEAVES.firstLeaf,
            LEAVES.secondLeaf
          )
        : null,
    [stageW]
  )
  const live = announce(RESPONSE, d)

  const toggle = (id: string) => {
    setRejected((r) => ({ ...r, [id]: !r[id] }))
    setHistory((h) => [...h, id])
  }
  const undo = () => {
    const last = history[history.length - 1]
    if (!last) return
    setRejected((r) => ({ ...r, [last]: !r[last] }))
    setHistory((h) => h.slice(0, -1))
  }
  const reset = () => {
    setRejected({})
    setHistory([])
  }
  const lastRow = history.length
    ? list.find((r) => r.id === history[history.length - 1])
    : undefined

  return (
    <div
      className="l-enter grid min-w-0 overflow-hidden rounded-2xl border bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_12px_32px_-12px_rgb(0_0_0/0.12)] md:grid-cols-[minmax(0,1fr)_16rem]"
      style={{ ["--d" as string]: "120ms" }}
      data-demo
    >
      <div className="flex min-w-0 items-center justify-end gap-2 border-b px-3 py-2.5 md:col-span-2">
        <p className="min-w-0 rounded-2xl rounded-br-md bg-primary px-3.5 py-2 text-sm text-primary-foreground">
          {QUESTION}
        </p>
      </div>

      {/* Plan */}
      <div className="flex min-w-0 items-center justify-center border-b bg-background p-2 sm:p-3 md:border-r md:border-b-0">
        <div
          ref={stageRef}
          className="plan-stage relative aspect-[1221/1220] w-full md:max-w-[min(100%,calc(100dvh-14rem),36rem)]"
        >
          <img
            src={bundledDrawing.images.rebuilt}
            alt={`Floor plan ${bundledDrawing.title}, ${bundledDrawing.subtitle}`}
            width={1221}
            height={1220}
            decoding="async"
            fetchPriority="high"
            className="l-fade absolute inset-0 size-full select-none"
            style={{ ["--d" as string]: "200ms" }}
            draggable={false}
          />
          {BOXES.map((b, i) => {
            const off = !!rejected[b.id]
            const isHot = hot === b.id
            const row = list[i]
            return (
              <button
                key={b.id}
                type="button"
                aria-pressed={off}
                aria-label={`${row.title} in ${row.sub}, ${off ? "rejected. Press to count it again" : "counted. Press to reject it"}`}
                onClick={() => toggle(b.id)}
                onPointerEnter={(e) =>
                  e.pointerType === "mouse" && setHot(b.id)
                }
                onPointerLeave={() => setHot(null)}
                style={{
                  left: `${b.x}%`,
                  top: `${b.y}%`,
                  width: `${b.w}%`,
                  height: `${b.h}%`,
                  animationDelay: `${BOX_START + i * BOX_STEP}ms`,
                }}
                className={cn(
                  "plan-evidence-in absolute min-h-2.5 min-w-2.5 rounded-[3px] border-2 p-0 outline-none",
                  "transition-[background-color,box-shadow,border-color] duration-150 ease-out",
                  "before:absolute before:top-1/2 before:left-1/2 before:size-full before:min-h-10 before:min-w-10 before:-translate-1/2",
                  "focus-visible:ring-3 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
                  off
                    ? "border-dashed border-warning bg-warning/10"
                    : "border-info bg-info/15 hover:bg-info/30",
                  isHot &&
                    (off
                      ? "z-[3] bg-warning/20 ring-4 ring-warning/30"
                      : "z-[3] bg-info/35 ring-4 ring-info/30")
                )}
              />
            )
          })}
          {badgeAt ? (
            <div
              aria-hidden
              className="pointer-events-none absolute inset-0 z-[4]"
            >
              {BOXES.map((b, i) => {
                const at = badgeAt.get(b.id) ?? { x: b.x, y: b.y, dx: 0, dy: 0 }
                const off = !!rejected[b.id]
                return (
                  <span
                    key={b.id}
                    className="plan-evidence-in absolute size-0"
                    style={{
                      left: `calc(${at.x}% + ${at.dx}px)`,
                      top: `calc(${at.y}% + ${at.dy}px)`,
                      animationDelay: `${BOX_START + i * BOX_STEP}ms`,
                    }}
                  >
                    <span
                      onClick={() => toggle(b.id)}
                      onPointerEnter={(e) =>
                        e.pointerType === "mouse" && setHot(b.id)
                      }
                      onPointerLeave={() => setHot(null)}
                      className={cn(
                        "num pointer-events-auto absolute -top-3 -left-3 flex h-6 min-w-6 cursor-pointer items-center justify-center rounded-full px-1 text-xs leading-none font-semibold",
                        "shadow-[0_0_0_2px_var(--background)] transition-transform duration-150 ease-out",
                        off
                          ? "border-[1.5px] border-dashed border-warning bg-background text-warning line-through decoration-2"
                          : "bg-info text-primary-foreground",
                        hot === b.id && "scale-110"
                      )}
                    >
                      {i + 1}
                    </span>
                  </span>
                )
              })}
            </div>
          ) : null}
        </div>
      </div>

      {/* Answer */}
      <div className="relative min-w-0">
        <article
          aria-label="Answer"
          className="l-fade flex min-w-0 flex-col gap-3 p-4 md:absolute md:inset-0"
          style={{
            ["--d" as string]: `${BOX_START + BOXES.length * BOX_STEP}ms`,
          }}
        >
          <div className="flex items-baseline gap-2">
            <span
              key={value?.[0]}
              className={cn(
                "num l-tick text-5xl leading-none font-semibold tracking-tight",
                d.changed && "text-warning"
              )}
            >
              {value?.[0]}
            </span>
            <span className="text-lg font-medium">{value?.[1]}</span>
          </div>
          <p className="text-sm text-muted-foreground">
            {sentence(RESPONSE, d, bundledDrawing, "en")}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {chipList.map((c) => (
              <Badge
                key={c.key}
                title={c.title}
                className={cn("h-6 px-2.5", CHIP_TONE[c.tone])}
              >
                {c.text}
              </Badge>
            ))}
          </div>

          <ul
            aria-label="Evidence"
            className="-mx-1 max-h-[11.5rem] min-h-0 flex-1 overflow-y-auto overscroll-contain border-t pt-1.5 md:max-h-none"
          >
            {list.map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  onClick={() => toggle(r.id)}
                  onPointerEnter={(e) =>
                    e.pointerType === "mouse" && setHot(r.id)
                  }
                  onPointerLeave={() => setHot(null)}
                  aria-pressed={!r.on}
                  aria-label={`${r.on ? "Reject" : "Count again"} ${r.title}, ${r.sub}`}
                  className={cn(
                    "flex min-h-10 w-full items-center gap-2.5 rounded-md px-1 text-left outline-none hover:bg-muted",
                    "transition-colors duration-150 focus-visible:ring-3 focus-visible:ring-ring",
                    hot === r.id && "bg-info-surface"
                  )}
                >
                  <span
                    className={cn(
                      "num inline-flex h-6 min-w-7 shrink-0 items-center justify-center rounded-md text-xs font-semibold",
                      r.on
                        ? "bg-info text-primary-foreground"
                        : "border-[1.5px] border-dashed border-warning text-warning"
                    )}
                  >
                    {r.badge}
                  </span>
                  <span
                    className={cn(
                      "min-w-0 flex-1 truncate text-sm",
                      !r.on && "text-muted-foreground line-through"
                    )}
                  >
                    {r.sub.split(" · ")[0]}
                  </span>
                  <span
                    className={cn(
                      "shrink-0 text-xs font-medium",
                      r.on ? "text-success" : "text-warning"
                    )}
                  >
                    {r.state}
                  </span>
                </button>
              </li>
            ))}
          </ul>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={undo}
              disabled={!history.length}
              className="l-press min-w-0"
            >
              <Undo2Icon data-icon="inline-start" />
              <span className="truncate">
                {lastRow ? `Undo ${lastRow.badge}` : "Undo"}
              </span>
            </Button>
            {history.length > 1 ? (
              <Button
                variant="ghost"
                size="icon"
                onClick={reset}
                aria-label="Reset the answer"
                title="Reset the answer"
                className="l-press text-muted-foreground"
              >
                <RotateCcwIcon />
              </Button>
            ) : null}
          </div>

          <p className="num flex flex-wrap gap-x-1.5 border-t border-dashed pt-2 text-xs text-muted-foreground">
            {footerParts(RESPONSE, "en")
              .filter((p) => !p.endsWith(" ms"))
              .map((p, i) => (
                <span key={i} className="whitespace-nowrap">
                  {i > 0 ? "· " : ""}
                  {p}
                </span>
              ))}
          </p>
          <p className="sr-only" aria-live="polite">
            {d.changed ? live : ""}
          </p>
        </article>
      </div>
    </div>
  )
}

export default HeroDemo

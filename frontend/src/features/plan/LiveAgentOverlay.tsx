import { useMemo } from 'react'
import { useUi } from '@/i18n/uiLang'
import { cn } from '@/lib/utils'
import { useActiveTurn, useAppStore } from '@/store'
import { STAGE_COPY, extractAgentTelemetry } from './badges'

const CORNER_PX = 12

/**
 * 60fps compositor-only CAD corner-bracket Crop Inspection Reticle (`[data-agent-reticle]`).
 * Mounted inside `[data-plan-stage]` so coordinates match `0..100%` of the drawing; animated strictly
 * via `transform: translate3d(...)` and `opacity` (snaps in 0ms under `prefers-reduced-motion: reduce`).
 */
export function LiveAgentReticle({ stageW, aspect }: { stageW: number; aspect: number }) {
  const turn = useActiveTurn()
  const drawingId = useAppStore((s) => s.session.drawing.id)
  const { lang } = useUi()
  const stream = turn?.status === 'pending' ? turn.stream : undefined

  const telemetry = useMemo(
    () => (stream ? extractAgentTelemetry(stream, drawingId, lang) : null),
    [stream, drawingId, lang],
  )

  const rect = telemetry?.focusRect
  if (!stream || !rect || stageW <= 0) return null

  const stageH = stageW / (aspect > 0 ? aspect : 1)
  const pxX = (rect.x / 100) * stageW
  const pxY = (rect.y / 100) * stageH
  const pxW = Math.max(28, (rect.w / 100) * stageW)
  const pxH = Math.max(28, (rect.h / 100) * stageH)
  const maxRight = Math.max(pxX, pxX + pxW - CORNER_PX)
  const maxBottom = Math.max(pxY, pxY + pxH - CORNER_PX)

  return (
    <div
      aria-hidden="true"
      data-agent-reticle=""
      className="pointer-events-none absolute inset-0 z-[15] overflow-hidden"
    >
      {/* Borderless soft fill sheet: scaling a borderless 100x100 quad runs on the GPU without distorting strokes */}
      <div
        className="plan-reticle-part absolute top-0 left-0 size-[100px] rounded-[2px] bg-info/10"
        style={{
          transformOrigin: '0 0',
          transform: `translate3d(${pxX}px, ${pxY}px, 0) scale(${pxW / 100}, ${pxH / 100})`,
        }}
      />
      {/* Four crisp 2px CAD corner brackets translated independently on the compositor */}
      <span
        className="plan-reticle-part absolute top-0 left-0 size-3 rounded-tl-[2px] border-t-2 border-l-2 border-info shadow-[-1px_-1px_0_1px_var(--background)]"
        style={{ transform: `translate3d(${pxX}px, ${pxY}px, 0)` }}
      />
      <span
        className="plan-reticle-part absolute top-0 left-0 size-3 rounded-tr-[2px] border-t-2 border-r-2 border-info shadow-[1px_-1px_0_1px_var(--background)]"
        style={{ transform: `translate3d(${maxRight}px, ${pxY}px, 0)` }}
      />
      <span
        className="plan-reticle-part absolute top-0 left-0 size-3 rounded-bl-[2px] border-b-2 border-l-2 border-info shadow-[-1px_1px_0_1px_var(--background)]"
        style={{ transform: `translate3d(${pxX}px, ${maxBottom}px, 0)` }}
      />
      <span
        className="plan-reticle-part absolute top-0 left-0 size-3 rounded-br-[2px] border-r-2 border-b-2 border-info shadow-[1px_1px_0_1px_var(--background)]"
        style={{ transform: `translate3d(${maxRight}px, ${maxBottom}px, 0)` }}
      />
    </div>
  )
}

/**
 * Compact top-left floating HUD pill (`[data-agent-hud]`) pinned to the stage viewport during streaming.
 */
export function LiveAgentHud({ offsetTop = false }: { offsetTop?: boolean }) {
  const turn = useActiveTurn()
  const drawingId = useAppStore((s) => s.session.drawing.id)
  const { lang } = useUi()
  const stream = turn?.status === 'pending' ? turn.stream : undefined

  const telemetry = useMemo(
    () => (stream ? extractAgentTelemetry(stream, drawingId, lang) : null),
    [stream, drawingId, lang],
  )

  if (!stream || !telemetry) return null

  const copy = STAGE_COPY[lang]
  let text: string
  if (telemetry.queuePosition !== undefined && telemetry.queuePosition > 0) {
    text = copy.queue(telemetry.queuePosition)
  } else {
    const parts = [copy.step(telemetry.stepIndex, telemetry.stepLabel)]
    if (telemetry.markedCount > 0) parts.push(copy.marked(telemetry.markedCount))
    if (telemetry.droppedCount > 0) parts.push(copy.dropped(telemetry.droppedCount))
    text = parts.join(' · ')
  }

  return (
    <div
      role="status"
      aria-live="polite"
      data-agent-hud=""
      className={cn(
        'pointer-events-none absolute left-3 z-30 flex max-w-[calc(100%-1.5rem)] items-center gap-2 rounded-full border bg-background/95 px-3 py-1.5 text-xs font-medium text-foreground shadow-sm',
        offsetTop ? 'top-16' : 'top-3',
      )}
    >
      <span aria-hidden="true" className="plan-candidate-pulse size-2 shrink-0 rounded-full bg-info" />
      <span className="truncate tabular-nums">{text}</span>
    </div>
  )
}

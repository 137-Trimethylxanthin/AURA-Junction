import { useState } from 'react'
import { safeUrl, type Box, type Drawing } from '@/api/contract'
import { cn } from '@/lib/utils'

export interface BoxCropThumbProps {
  box: Box
  drawing: Drawing
  /** Optional drawing when this box belongs to a sibling floor in a project bundle. */
  targetDrawing?: Drawing | null
  /** If set and different from `drawing.id` without `targetDrawing`, omit crop rather than cropping the wrong floor. */
  boxDrawingId?: string
  /** Whether the box is currently counted (`true`) or rejected (`false`). */
  on?: boolean
  /** Optional CSS color (e.g. `var(--group-2, #6d3ab7)`) for grouped evidence boxes. */
  accentColor?: string
  className?: string
}

/**
 * Zero-network-fetch 56x56 px inline blueprint crop around an evidence box.
 * Reuses the browser-cached `drawing.images.rebuilt` inside an `<svg viewBox>` window.
 */
export function BoxCropThumb({
  box,
  drawing,
  targetDrawing,
  boxDrawingId,
  on = true,
  accentColor,
  className,
}: BoxCropThumbProps) {
  const [failedHref, setFailedHref] = useState<string | null>(null)
  const resolvedDrawing =
    boxDrawingId && boxDrawingId !== drawing.id ? (targetDrawing ?? null) : (targetDrawing ?? drawing)
  const href = safeUrl(resolvedDrawing?.images.rebuilt)
  if (!resolvedDrawing || !href || failedHref === href) return null

  const [rawW, rawH] = resolvedDrawing.frame.px
  const W = Number.isFinite(rawW) && rawW > 0 ? rawW : 1000
  const H = Number.isFinite(rawH) && rawH > 0 ? rawH : 1000

  const bx = (box.x / 100) * W
  const by = (box.y / 100) * H
  const bw = Math.max(2, (box.w / 100) * W)
  const bh = Math.max(2, (box.h / 100) * H)
  const cx = bx + bw / 2
  const cy = by + bh / 2

  const minFloor = Math.min(W, H)
  const minSide = Math.min(minFloor, Math.max(24, minFloor * 0.06))
  const side = Math.min(minFloor, Math.max(minSide, Math.max(bw, bh) * 2.4))
  const vx = Math.max(0, Math.min(W - side, cx - side / 2))
  const vy = Math.max(0, Math.min(H - side, cy - side / 2))

  const strokePx = (side / 56) * 1.75
  const stroke = on ? (accentColor ?? 'var(--info)') : 'var(--warning)'

  return (
    <span
      aria-hidden="true"
      data-box-crop={box.id}
      className={cn(
        'inline-flex size-14 shrink-0 overflow-hidden rounded-md border border-border bg-muted/40 shadow-2xs',
        className,
      )}
    >
      <svg
        viewBox={`${vx} ${vy} ${side} ${side}`}
        className="size-full"
        focusable="false"
        aria-hidden="true"
      >
        <image
          href={href}
          x={0}
          y={0}
          width={W}
          height={H}
          preserveAspectRatio="none"
          onError={() => setFailedHref(href)}
        />
        <rect
          x={bx}
          y={by}
          width={bw}
          height={bh}
          rx={strokePx * 1.2}
          fill={stroke}
          fillOpacity={on ? 0.14 : 0.06}
          stroke={stroke}
          strokeWidth={strokePx}
          strokeDasharray={on ? undefined : `${strokePx * 2.2} ${strokePx * 1.6}`}
        />
      </svg>
    </span>
  )
}

import type { Box } from "@/api/contract"
import { bundledDrawing } from "@/data/drawing"
import { cn } from "@/lib/utils"

/** A zoomed square of the real plan around one evidence box: what the Gemini tile check is shown. */
export function PlanTile({
  box,
  zoom = 4,
  className,
}: {
  box: Box
  zoom?: number
  className?: string
}) {
  const cx = box.x + box.w / 2
  const cy = box.y + box.h / 2
  return (
    <div
      className={cn(
        "relative size-28 shrink-0 overflow-hidden rounded-md border bg-background",
        className
      )}
    >
      <img
        src={bundledDrawing.images.rebuilt}
        alt=""
        width={1221}
        height={1220}
        loading="lazy"
        decoding="async"
        draggable={false}
        className="absolute max-w-none select-none"
        style={{
          width: `${zoom * 100}%`,
          left: `${50 - cx * zoom}%`,
          top: `${50 - cy * zoom}%`,
        }}
      />
      <span
        aria-hidden
        className="absolute rounded-[3px] border-2 border-info bg-info/15"
        style={{
          left: `${50 - (box.w * zoom) / 2}%`,
          top: `${50 - (box.h * zoom) / 2}%`,
          width: `${box.w * zoom}%`,
          height: `${box.h * zoom}%`,
        }}
      />
    </div>
  )
}

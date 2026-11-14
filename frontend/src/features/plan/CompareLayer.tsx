import { useCallback, useEffect, useRef, type KeyboardEvent, type PointerEvent } from 'react'
import { ChevronsLeftRightIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAppStore, useAppStoreShallow } from '@/store'
import { useUi } from '@/i18n/uiLang'
import { pctPoint, swipeSplit, type StageRef } from './shared'

const clampSplit = (v: number) => Math.min(98, Math.max(2, v))

/** Position of the swipe divider, shared with the context menu ("Compare with the original here"). */

/** Original drawing over the rebuilt one: a swipe divider (default) or a multiply blend. */
export function CompareLayer({ stageRef }: { stageRef: StageRef }) {
  const { tu } = useUi()
  const src = useAppStore((s) => s.session.drawing.images.original)
  // The right-hand label names what the rebuilt plan was made from: a scan is not CAD.
  const kind = useAppStore((s) => s.session.drawing.kind)
  const { on, mode, opacity } = useAppStoreShallow((s) => s.ui.compare)
  const imgRef = useRef<HTMLImageElement>(null)
  const divRef = useRef<HTMLDivElement>(null)
  const dragId = useRef<number | null>(null)

  const apply = useCallback(
    (v: number) => {
      swipeSplit.current = clampSplit(v)
      const s = swipeSplit.current
      if (imgRef.current && mode === 'swipe') imgRef.current.style.clipPath = `inset(0 ${100 - s}% 0 0)`
      if (divRef.current) {
        const rounded = Math.round(s)
        divRef.current.style.left = `${s}%`
        divRef.current.setAttribute('aria-valuenow', String(rounded))
        divRef.current.setAttribute('aria-valuetext', tu('cmp_pct', { n: rounded }))
      }
    },
    [mode, tu],
  )

  // Re-apply the stored split after mounting or switching back to swipe.
  useEffect(() => {
    if (on) apply(swipeSplit.current)
  }, [on, apply])

  if (!on || !src) return null
  const swipe = mode === 'swipe'

  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    e.stopPropagation() // no long-press menu, no stage click
    if (dragId.current !== null) return
    dragId.current = e.pointerId
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (dragId.current !== e.pointerId || !stageRef.current) return
    apply(pctPoint(stageRef.current, e.clientX, e.clientY)[0])
  }
  const onUp = (e: PointerEvent<HTMLDivElement>) => {
    if (dragId.current === e.pointerId) dragId.current = null
  }
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = { ArrowLeft: -2, ArrowDown: -2, ArrowRight: 2, ArrowUp: 2 }[e.key]
    if (step !== undefined) {
      e.preventDefault()
      apply(swipeSplit.current + step)
    } else if (e.key === 'Home') {
      e.preventDefault()
      apply(2)
    } else if (e.key === 'End') {
      e.preventDefault()
      apply(98)
    }
  }

  return (
    <>
      <img
        ref={imgRef}
        src={src}
        alt={tu('cmp_alt')}
        draggable={false}
        className={cn(
          'pointer-events-none absolute inset-0 size-full bg-card object-fill select-none',
          !swipe && 'mix-blend-multiply',
        )}
        style={
          swipe
            ? { clipPath: `inset(0 ${100 - swipeSplit.current}% 0 0)` }
            : { opacity: opacity / 100, clipPath: 'none', background: 'transparent' }
        }
      />
      {swipe ? (
        <div
          ref={divRef}
          role="slider"
          tabIndex={0}
          aria-label={tu('cmp_divider')}
          aria-orientation="horizontal"
          aria-valuemin={2}
          aria-valuemax={98}
          aria-valuenow={Math.round(swipeSplit.current)}
          aria-valuetext={tu('cmp_pct', { n: Math.round(swipeSplit.current) })}
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerCancel={onUp}
          onClick={(e) => e.stopPropagation()}
          onContextMenu={(e) => {
            e.preventDefault()
            e.stopPropagation()
          }}
          onKeyDown={onKey}
          className="group/divider absolute inset-y-0 z-30 w-11 -translate-x-1/2 cursor-ew-resize touch-none outline-none"
          style={{ left: `${swipeSplit.current}%` }}
        >
          <span aria-hidden className="absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-foreground" />
          <span
            aria-hidden
            className={cn(
              'absolute top-1/2 left-1/2 flex size-11 -translate-1/2 items-center justify-center rounded-full bg-foreground text-background',
              'shadow-[0_4px_14px_-2px_color-mix(in_oklch,var(--foreground)_35%,transparent)] transition-transform duration-150 ease-out',
              'group-active/divider:scale-95 group-focus-visible/divider:ring-3 group-focus-visible/divider:ring-ring/60 group-focus-visible/divider:ring-offset-2',
            )}
          >
            <ChevronsLeftRightIcon className="size-5" />
          </span>
        </div>
      ) : null}
      {swipe ? (
        // Side labels in the stage's bottom corners: the drawing's margin, clear of evidence and marks.
        <>
          <span className="pointer-events-none absolute bottom-2 left-2 z-30 rounded-sm bg-foreground px-2 py-0.5 text-xs font-semibold whitespace-nowrap text-background">
            {tu('cmp_original')}
          </span>
          <span className="pointer-events-none absolute right-2 bottom-2 z-30 rounded-sm border border-foreground bg-background px-2 py-0.5 text-xs font-semibold whitespace-nowrap text-foreground">
            {tu(kind === 'raster' ? 'cmp_rebuilt_scan' : kind === 'ifc' ? 'cmp_rebuilt_model' : 'cmp_rebuilt')}
          </span>
        </>
      ) : null}
    </>
  )
}

import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * One horizontally scrolling line of chips. Edges fade and show a small arrow button when more chips
 * are hidden; a vertical mouse wheel scrolls sideways; Tab into a chip scrolls it into view.
 */
export function ScrollRow({ children, className, label }: { children: React.ReactNode; className?: string; label: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const inner = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ start: false, end: false })

  const measure = useCallback(() => {
    const el = ref.current
    if (!el) return
    const start = el.scrollLeft > 2
    const end = el.scrollLeft + el.clientWidth < el.scrollWidth - 2
    setEdges((p) => (p.start === start && p.end === end ? p : { start, end }))
  }, [])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    if (inner.current) ro.observe(inner.current)
    const onWheel = (e: WheelEvent) => {
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX) || el.scrollWidth <= el.clientWidth) return
      e.preventDefault()
      el.scrollLeft += e.deltaY
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      ro.disconnect()
      el.removeEventListener('wheel', onWheel)
    }
  }, [measure])

  const page = (dir: 1 | -1) => {
    const el = ref.current
    if (el) el.scrollBy({ left: dir * el.clientWidth * 0.8, behavior: 'smooth' })
  }

  const fade = '2.5rem'
  const mask =
    edges.start || edges.end
      ? `linear-gradient(to right, ${edges.start ? `transparent, black ${fade}` : 'black'}, ${edges.end ? `black calc(100% - ${fade}), transparent` : 'black'})`
      : undefined

  return (
    <div className={cn('relative -mx-3', className)} role="group" aria-label={label}>
      <div
        ref={ref}
        onScroll={measure}
        className="-my-1 flex items-center gap-1.5 overflow-x-auto scroll-px-3 px-3 py-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        style={{ maskImage: mask, WebkitMaskImage: mask }}
      >
        <div ref={inner} className="flex w-max shrink-0 items-center gap-1.5">
          {children}
        </div>
      </div>
      {edges.start ? (
        <button
          type="button"
          tabIndex={-1}
          aria-hidden
          onClick={() => page(-1)}
          className="absolute top-1/2 left-1 grid size-8 -translate-y-1/2 place-items-center rounded-full border bg-background text-muted-foreground shadow-sm hover:text-foreground"
        >
          <ChevronLeftIcon className="size-4" />
        </button>
      ) : null}
      {edges.end ? (
        <button
          type="button"
          tabIndex={-1}
          aria-hidden
          onClick={() => page(1)}
          className="absolute top-1/2 right-1 grid size-8 -translate-y-1/2 place-items-center rounded-full border bg-background text-muted-foreground shadow-sm hover:text-foreground"
        >
          <ChevronRightIcon className="size-4" />
        </button>
      ) : null}
    </div>
  )
}

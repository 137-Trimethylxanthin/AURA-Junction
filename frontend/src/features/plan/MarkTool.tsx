import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { rectFromPoints } from '@/lib/geometry'
import { cn } from '@/lib/utils'
import { actions, useAppStore } from '@/store'
import { useUi } from '@/i18n/uiLang'
import { markLabel, nextLabel, overlayOpen, pctPoint, type StageRef } from './shared'

// Re-exported for compatibility with pre-Wave-2 Composer imports before Lane F2 merges.
export { markLabel }

const MIN_PCT = 1.5

/** Marked areas, in the user colour. The attached one (sent with the next question) is emphasised. */
export function MarksLayer() {
  const { tu } = useUi()
  const marks = useAppStore((s) => s.ui.marks)
  const attached = useAppStore((s) => s.ui.attachedMarkId)
  const tool = useAppStore((s) => s.ui.tool)
  if (marks.length === 0) return null
  return (
    // Hidden (not unmounted) while resizing a room: chips would cover the edge handle, and a remount
    // would replay the entrance animation.
    <div className={cn('pointer-events-none absolute inset-0', tool === 'whatif' && 'invisible')} data-layer="marks">
      {marks.map((m) => {
        const on = m.id === attached
        return (
          <div
            key={m.id}
            data-mark-id={m.id}
            className={cn(
              'plan-evidence-in absolute rounded-md border-2 border-dashed border-user',
              on ? 'bg-user/12 ring-4 ring-user/20' : 'bg-user/5 opacity-70',
            )}
            style={{ left: `${m.rect.x}%`, top: `${m.rect.y}%`, width: `${m.rect.w}%`, height: `${m.rect.h}%` }}
          >
            <span
              className={cn(
                'absolute -top-6 -left-0.5 flex h-5 items-center gap-1 rounded-sm px-1.5 text-xs font-semibold whitespace-nowrap',
                on ? 'bg-user text-primary-foreground' : 'border border-user bg-background text-user',
              )}
            >
              {markLabel(m.label, tu)}
              {on ? <span className="font-normal opacity-85">{tu('mark_attached')}</span> : null}
            </span>
          </div>
        )
      })}
    </div>
  )
}

type Kb = { cursor: [number, number]; anchor: [number, number] | null }

const clampPct = (v: number) => Math.min(100, Math.max(0, v))
const fmtPct = (v: number) => `${Math.round(v)} %`

/**
 * Capture layer while the Mark tool is active.
 * Pointer: drag a rectangle, release to attach it; two fingers pan the plan instead.
 * Keyboard: arrows move a crosshair (Shift = bigger steps), Enter/Space sets the first corner,
 * arrows size the rectangle, Enter confirms, Escape steps back.
 */
export function MarkCapture({ stageRef }: { stageRef: StageRef }) {
  const { tu } = useUi()
  const layerRef = useRef<HTMLDivElement>(null)
  const draftEl = useRef<HTMLDivElement>(null)
  const drag = useRef<{ id: number; a: [number, number]; b: [number, number]; raf: number } | null>(null)
  const touches = useRef(new Map<number, [number, number]>())
  const [kb, setKb] = useState<Kb | null>(null)
  const [say, setSay] = useState('')

  const paint = useCallback(() => {
    const d = drag.current
    const el = draftEl.current
    if (!el) return
    if (d) d.raf = 0
    if (!d) {
      el.style.display = 'none'
      return
    }
    const r = rectFromPoints(d.a, d.b)
    el.style.display = 'block'
    el.style.left = `${r.x}%`
    el.style.top = `${r.y}%`
    el.style.width = `${r.w}%`
    el.style.height = `${r.h}%`
  }, [])

  const cancel = useCallback(() => {
    const d = drag.current
    if (d?.raf) cancelAnimationFrame(d.raf)
    drag.current = null
    paint()
  }, [paint])

  const finish = useCallback(
    (a: [number, number], b: [number, number]) => {
      const r = rectFromPoints(a, b)
      if (r.w < MIN_PCT || r.h < MIN_PCT) return false
      const s = actions()
      s.addMark(r, nextLabel('Area'))
      s.setTool('select')
      stageRef.current?.focus({ preventScroll: true })
      return true
    },
    [stageRef],
  )

  const leave = useCallback(() => {
    actions().setTool('select')
    stageRef.current?.focus({ preventScroll: true })
  }, [stageRef])

  // Focus the layer so the keyboard path works right after picking the tool.
  useEffect(() => {
    layerRef.current?.focus({ preventScroll: true })
  }, [])

  // Escape when focus is elsewhere (e.g. still on the toolbar): pointer draft first, then leave the tool.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || overlayOpen()) return
      if (e.target === layerRef.current) return // handled by onKeyDown
      e.preventDefault()
      if (drag.current) cancel()
      else leave()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [cancel, leave])

  // ---- pointer --------------------------------------------------------------
  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === 'touch') {
      touches.current.set(e.pointerId, [e.clientX, e.clientY])
      if (touches.current.size >= 2) {
        cancel() // second finger: this is a pan, not a mark
        return
      }
    }
    if (drag.current || !stageRef.current) return
    if (e.pointerType === 'mouse' && e.button !== 0) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    const p = pctPoint(stageRef.current, e.clientX, e.clientY)
    drag.current = { id: e.pointerId, a: p, b: p, raf: 0 }
    setKb(null)
  }
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    const t = touches.current
    if (e.pointerType === 'touch' && t.size >= 2 && t.has(e.pointerId)) {
      // Two-finger pan: scroll by the movement of this finger, averaged over the fingers down.
      const prev = t.get(e.pointerId)!
      const sc = stageRef.current?.closest<HTMLElement>('[data-plan-scroller]')
      sc?.scrollBy((prev[0] - e.clientX) / t.size, (prev[1] - e.clientY) / t.size)
      t.set(e.pointerId, [e.clientX, e.clientY])
      return
    }
    const d = drag.current
    if (!d || d.id !== e.pointerId || !stageRef.current) return
    d.b = pctPoint(stageRef.current, e.clientX, e.clientY)
    if (!d.raf) d.raf = requestAnimationFrame(paint)
  }
  const onUp = (e: PointerEvent<HTMLDivElement>) => {
    touches.current.delete(e.pointerId)
    const d = drag.current
    if (!d || d.id !== e.pointerId) return
    cancel()
    finish(d.a, d.b)
  }
  const onCancel = (e: PointerEvent<HTMLDivElement>) => {
    touches.current.delete(e.pointerId)
    cancel()
  }

  // ---- keyboard -------------------------------------------------------------
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 5 : 1
    const move = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key]
    const cur: Kb = kb ?? { cursor: [50, 50], anchor: null }
    if (move) {
      e.preventDefault()
      const cursor: [number, number] = [clampPct(cur.cursor[0] + move[0]), clampPct(cur.cursor[1] + move[1])]
      setKb({ ...cur, cursor })
      return
    }
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      if (!kb) {
        setKb(cur)
        setSay(tu('mark_kb_start'))
      } else if (!cur.anchor) {
        setKb({ ...cur, anchor: cur.cursor })
        setSay(tu('mark_kb_corner', { x: fmtPct(cur.cursor[0]), y: fmtPct(cur.cursor[1]) }))
      } else if (!finish(cur.anchor, cur.cursor)) {
        setSay(tu('mark_kb_small'))
      }
      return
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      if (drag.current) cancel()
      else if (kb?.anchor) {
        setKb({ ...kb, anchor: null })
        setSay(tu('mark_kb_cleared'))
      } else leave()
    }
  }

  const kbRect = kb?.anchor ? rectFromPoints(kb.anchor, kb.cursor) : null

  return (
    <div
      ref={layerRef}
      role="application"
      tabIndex={0}
      aria-label={tu('mark_capture')}
      aria-describedby="plan-mark-help"
      className="group/mark absolute inset-0 z-20 cursor-crosshair touch-none bg-user/5 outline-none focus-visible:ring-3 focus-visible:ring-user/50 focus-visible:ring-inset"
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onCancel}
      onKeyDown={onKeyDown}
      onContextMenu={(e) => e.preventDefault()}
    >
      <span id="plan-mark-help" className="sr-only">
        {tu('mark_help')}
      </span>
      <span className="sr-only" aria-live="polite">
        {say}
      </span>
      <div
        ref={draftEl}
        className="pointer-events-none absolute hidden rounded-md border-2 border-dashed border-user bg-user/10"
      />
      {kbRect ? (
        <div
          className="pointer-events-none absolute rounded-md border-2 border-dashed border-user bg-user/10"
          style={{ left: `${kbRect.x}%`, top: `${kbRect.y}%`, width: `${kbRect.w}%`, height: `${kbRect.h}%` }}
        />
      ) : null}
      {kb ? (
        <div
          aria-hidden
          className="pointer-events-none absolute size-0"
          style={{ left: `${kb.cursor[0]}%`, top: `${kb.cursor[1]}%` }}
        >
          <span className="absolute -top-4 left-0 h-8 w-0.5 -translate-x-1/2 bg-user" />
          <span className="absolute top-0 -left-4 h-0.5 w-8 -translate-y-1/2 bg-user" />
          <span className="absolute -top-1.5 -left-1.5 size-3 rounded-full border-2 border-user bg-background" />
        </div>
      ) : null}
    </div>
  )
}

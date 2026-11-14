import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useInRouterContext, useNavigate, type NavigateFunction } from 'react-router'
import { LayersIcon } from 'lucide-react'
import { getDrawing } from '@/api/client'
import { safeUrl } from '@/api/contract'
import { isBoxRejected, isRoomIncluded } from '@/lib/answer'
import { cn } from '@/lib/utils'
import { useUi } from '@/i18n/uiLang'
import { actions, useActiveTurn, useAppStore } from '@/store'
import {
  STAGE_COPY,
  readActiveProject,
  readCrossPlanScope,
  type GroupedBox,
  type GroupedRoom,
  type ProjectBundleLike,
} from './badges'

const prefetchedFloorIds = new Set<string>()

function RouterNavSink({ navRef }: { navRef: React.RefObject<NavigateFunction | null> }) {
  const nav = useNavigate()
  useLayoutEffect(() => {
    navRef.current = nav
    return () => {
      navRef.current = null
    }
  }, [nav, navRef])
  return null
}

function readStoredProjectForDrawing(drawingId: string): ProjectBundleLike | null {
  try {
    const raw = globalThis.localStorage?.getItem('aura.projects')
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return null
    for (const p of parsed) {
      if (
        p &&
        typeof p === 'object' &&
        typeof (p as ProjectBundleLike).id === 'string' &&
        Array.isArray((p as ProjectBundleLike).floors) &&
        (p as ProjectBundleLike).floors.some((f) => f && f.drawing_id === drawingId)
      ) {
        return p as ProjectBundleLike
      }
    }
  } catch {
    /* ignore storage parse failures */
  }
  return null
}

function ensureImagePrefetchLink(href: string, floorDrawingId: string) {
  if (typeof document === 'undefined') return
  const safe = safeUrl(href)
  if (!safe) return
  if (document.head.querySelector(`link[data-aura-prefetch="${CSS.escape(floorDrawingId)}"]`)) return
  const link = document.createElement('link')
  link.rel = 'prefetch'
  link.as = 'image'
  link.href = safe
  link.setAttribute('data-aura-prefetch', floorDrawingId)
  document.head.appendChild(link)
}

export interface FloorSwitcherProps {
  /** Optional explicit project override (used in isolated tests; defaults to activeProject in store). */
  project?: ProjectBundleLike | null
  /** Optional callback fired when switching floors. */
  onNavigateFloor?: (drawingId: string) => void
}

/**
 * Fast segmented Floor Switcher (`[KG] [EG ●] [1. OG · 2]`) + `"All N floors"` (`crossPlanScope`) pill.
 * Renders whenever the active House/Project bundle has `>= 2` floors, pre-warming sibling drawings & plan images.
 */
export function FloorSwitcher({ project: projectProp, onNavigateFloor }: FloorSwitcherProps = {}) {
  const drawingId = useAppStore((s) => s.session.drawing.id)
  const storeProject = useAppStore(readActiveProject)
  const storeCrossScope = useAppStore(readCrossPlanScope)
  const sourceKind = useAppStore((s) => s.session.source.kind)
  const turn = useActiveTurn()
  const { lang } = useUi()
  const copy = STAGE_COPY[lang]

  const inRouter = useInRouterContext()
  const navRef = useRef<NavigateFunction | null>(null)
  const [localCrossScope, setLocalCrossScope] = useState(false)

  const project = useMemo(
    () => (projectProp !== undefined ? projectProp : storeProject ?? readStoredProjectForDrawing(drawingId)),
    [projectProp, storeProject, drawingId],
  )

  const floors = useMemo(
    () => (project && project.floors.length >= 2 ? [...project.floors].sort((a, b) => a.order - b.order) : []),
    [project],
  )

  const hasStoreCrossAction = typeof (actions() as unknown as { setCrossPlanScope?: unknown }).setCrossPlanScope === 'function'
  const crossPlanScope = hasStoreCrossAction ? storeCrossScope : storeCrossScope || localCrossScope

  // Pre-warm sibling floor Drawing JSON + `<link rel="prefetch" as="image">` in the background.
  useEffect(() => {
    if (floors.length < 2) return
    const baseUrl = ((import.meta.env?.VITE_API_URL as string | undefined) ?? '').trim()
    if (!baseUrl && sourceKind !== 'api') return
    const apiBase = baseUrl.replace(/\/+$/, '')
    let cancelled = false

    const warm = () => {
      for (const floor of floors) {
        if (floor.drawing_id === drawingId || prefetchedFloorIds.has(floor.drawing_id)) continue
        prefetchedFloorIds.add(floor.drawing_id)
        if (!apiBase) continue
        void getDrawing({ baseUrl: apiBase }, floor.drawing_id)
          .then((d) => {
            if (cancelled) return
            const rawImg = safeUrl(d.images.rebuilt)
            if (!rawImg) return
            const resolved = rawImg.startsWith('/') ? `${apiBase}${rawImg}` : rawImg
            ensureImagePrefetchLink(resolved, floor.drawing_id)
          })
          .catch(() => {
            prefetchedFloorIds.delete(floor.drawing_id)
          })
      }
    }

    const timer = window.setTimeout(warm, 40)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [floors, drawingId, sourceKind])

  // Compute live per-floor evidence hit counts from the active turn.
  const hitsByFloor = useMemo(() => {
    const counts = new Map<string, number>()
    const ev = turn?.status === 'done' ? turn.response?.evidence : turn?.stream?.evidence
    if (!turn || !ev) return counts
    const hasScopedItem =
      (ev.boxes as GroupedBox[]).some((b) => Boolean(b.drawing_id)) ||
      (ev.rooms as GroupedRoom[]).some((r) => Boolean(r.drawing_id))

    for (const b of ev.boxes as GroupedBox[]) {
      if (isBoxRejected(b.id, turn.toggles)) continue
      const fid = b.drawing_id ?? (hasScopedItem ? undefined : drawingId)
      if (fid) counts.set(fid, (counts.get(fid) ?? 0) + 1)
    }
    for (const r of ev.rooms as GroupedRoom[]) {
      if (!isRoomIncluded(r, turn.toggles)) continue
      const fid = r.drawing_id ?? (hasScopedItem ? undefined : drawingId)
      if (fid) counts.set(fid, (counts.get(fid) ?? 0) + 1)
    }
    return counts
  }, [turn, drawingId])

  const switchFloor = useCallback(
    (targetId: string) => {
      onNavigateFloor?.(targetId)
      if (targetId === drawingId) return
      if (navRef.current) {
        void navRef.current(`/d/${encodeURIComponent(targetId)}`)
      } else {
        void actions().loadDrawing(targetId)
      }
    },
    [drawingId, onNavigateFloor],
  )

  const toggleCrossPlan = useCallback(() => {
    const next = !crossPlanScope
    const storeAct = (actions() as unknown as { setCrossPlanScope?: (on: boolean) => void }).setCrossPlanScope
    if (typeof storeAct === 'function') {
      storeAct(next)
    } else {
      setLocalCrossScope(next)
    }
  }, [crossPlanScope])

  if (floors.length < 2) return null

  const multiFloorHits = [...hitsByFloor.keys()].some((fid) => fid !== drawingId)

  return (
    <div
      role="navigation"
      aria-label={copy.floorSwitcherAria}
      data-floor-switcher=""
      className="pointer-events-auto absolute top-3 left-1/2 z-30 flex max-w-[calc(100%-1.5rem)] -translate-x-1/2 items-center gap-1 overflow-x-auto rounded-xl border bg-background/95 p-1 shadow-sm backdrop-blur-xs"
    >
      {inRouter ? <RouterNavSink navRef={navRef} /> : null}
      <div className="flex items-center gap-0.5" role="group" aria-label={project?.title || copy.floorSwitcherAria}>
        {floors.map((floor) => {
          const active = floor.drawing_id === drawingId
          const count = hitsByFloor.get(floor.drawing_id) ?? 0
          const showCount = count > 0 && (!active || multiFloorHits)
          return (
            <button
              key={floor.drawing_id}
              type="button"
              data-floor-id={floor.drawing_id}
              aria-current={active ? 'page' : undefined}
              onClick={() => switchFloor(floor.drawing_id)}
              className={cn(
                'flex h-10 min-h-10 min-w-10 shrink-0 items-center justify-center gap-1.5 rounded-lg px-3 text-xs font-medium whitespace-nowrap transition-colors outline-none',
                'focus-visible:ring-3 focus-visible:ring-ring/60',
                active
                  ? 'bg-primary text-primary-foreground shadow-xs'
                  : 'text-foreground hover:bg-muted',
              )}
            >
              <span>{floor.label}</span>
              {active && !showCount ? (
                <span aria-hidden="true" className="size-1.5 rounded-full bg-primary-foreground" />
              ) : null}
              {showCount ? (
                <span
                  data-floor-hits={count}
                  className={cn(
                    'num rounded-full px-1.5 py-0.5 text-xs leading-none font-semibold',
                    active ? 'bg-primary-foreground/20 text-primary-foreground' : 'bg-info-surface text-info',
                  )}
                >
                  {count}
                </span>
              ) : null}
            </button>
          )
        })}
      </div>
      <div aria-hidden="true" className="mx-0.5 h-5 w-px shrink-0 bg-border" />
      <button
        type="button"
        data-cross-plan-toggle=""
        aria-pressed={crossPlanScope}
        onClick={toggleCrossPlan}
        className={cn(
          'flex h-10 min-h-10 min-w-10 shrink-0 items-center gap-1.5 rounded-lg border px-3 text-xs font-medium whitespace-nowrap transition-colors outline-none',
          'focus-visible:ring-3 focus-visible:ring-ring/60',
          crossPlanScope
            ? 'border-user bg-user text-primary-foreground shadow-xs hover:bg-user/90'
            : 'border-transparent text-muted-foreground hover:bg-muted hover:text-foreground',
        )}
      >
        <LayersIcon aria-hidden="true" className="size-3.5 shrink-0" />
        <span>{copy.allFloors(floors.length)}</span>
      </button>
    </div>
  )
}

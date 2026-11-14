import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useInRouterContext, useNavigate, type NavigateFunction } from 'react-router'
import { LayersIcon } from 'lucide-react'
import { getDrawing } from '@/api/client'
import type { Drawing } from '@/api/contract'
import { apiUrl, myProjects } from '@/api/library'
import { isBoxRejected, isRoomIncluded } from '@/lib/answer'
import { cn } from '@/lib/utils'
import { useUi } from '@/i18n/uiLang'
import { actions, useActiveTurn, useAppStore } from '@/store'
import {
  STAGE_COPY,
  readActiveProject,
  readCrossPlanScope,
  resolveProjectForDrawing,
  type GroupedBox,
  type GroupedRoom,
  type ProjectBundleLike,
} from './badges'

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

function ensureImagePrefetchLink(href: string | null, floorDrawingId: string) {
  if (typeof document === 'undefined' || !href) return
  if (document.head.querySelector(`link[data-aura-prefetch="${CSS.escape(floorDrawingId)}"]`)) return
  const link = document.createElement('link')
  link.rel = 'prefetch'
  link.as = 'image'
  link.href = href
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
  const prefetchedRef = useRef(new Set<string>())
  const [localCrossScope, setLocalCrossScope] = useState(false)
  const [projRev, setProjRev] = useState(0)

  useEffect(() => myProjects.subscribe(() => setProjRev((r) => r + 1)), [])

  const project = useMemo(() => {
    void projRev
    return projectProp !== undefined ? projectProp : resolveProjectForDrawing(storeProject, drawingId)
  }, [projectProp, storeProject, drawingId, projRev])

  const floors = useMemo(
    () => (project && project.floors.length >= 2 ? [...project.floors].sort((a, b) => a.order - b.order) : []),
    [project],
  )

  const hasStoreCrossAction = typeof (actions() as unknown as { setCrossPlanScope?: unknown }).setCrossPlanScope === 'function'
  const crossPlanScope = hasStoreCrossAction ? storeCrossScope : storeCrossScope || localCrossScope

  // Pre-warm sibling floor Drawing JSON into `appStore.drawingById` + `<link rel="prefetch" as="image">`.
  useEffect(() => {
    if (floors.length < 2) return
    const baseUrl = ((import.meta.env?.VITE_API_URL as string | undefined) ?? '').trim()
    const apiBase = baseUrl.replace(/\/+$/, '')
    const warmFn = (actions() as unknown as { warmDrawing?: (id: string) => Promise<Drawing | null> }).warmDrawing
    if (sourceKind !== 'api' && !apiBase && typeof warmFn !== 'function') return
    let cancelled = false

    const warm = () => {
      for (const floor of floors) {
        const fid = floor.drawing_id
        if (fid === drawingId || prefetchedRef.current.has(fid)) continue
        prefetchedRef.current.add(fid)

        const task: Promise<Drawing | null> =
          typeof warmFn === 'function'
            ? warmFn(fid)
            : sourceKind === 'api' || Boolean(apiBase)
              ? getDrawing({ baseUrl: apiBase }, fid)
              : Promise.resolve(null)

        void task
          .then((d) => {
            if (!d) {
              prefetchedRef.current.delete(fid)
              return
            }
            if (cancelled) return
            ensureImagePrefetchLink(apiUrl(d.images.rebuilt, apiBase || null), fid)
          })
          .catch(() => {
            prefetchedRef.current.delete(fid)
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

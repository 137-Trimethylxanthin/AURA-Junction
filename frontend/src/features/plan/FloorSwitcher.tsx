import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useInRouterContext, useNavigate, type NavigateFunction } from 'react-router'
import { ChevronDownIcon, ChevronUpIcon, LayersIcon } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
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

const RAIL_COPY: Record<'en' | 'fr' | 'fi' | 'de', { floorUp: string; floorDown: string }> = {
  en: { floorUp: 'Floor up', floorDown: 'Floor down' },
  fr: { floorUp: 'Étage supérieur', floorDown: 'Étage inférieur' },
  fi: { floorUp: 'Kerros ylös', floorDown: 'Kerros alas' },
  de: { floorUp: 'Stockwerk hoch', floorDown: 'Stockwerk runter' },
}

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
  const copy = { ...STAGE_COPY[lang], ...RAIL_COPY[lang] }
  const reduced = useReducedMotion() ?? false

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
  const curIdx = floors.findIndex((f) => f.drawing_id === drawingId)
  // Highest floor first, the way a building and a lift panel read.
  const topDown = floors.map((f, i) => ({ f, level: i + 1 })).reverse()
  const step = (delta: 1 | -1) => {
    const next = floors[curIdx + delta]
    if (next) switchFloor(next.drawing_id)
  }

  return (
    <nav
      aria-label={copy.floorSwitcherAria}
      data-floor-switcher=""
      className="floor-rail pointer-events-auto absolute top-1/2 left-3 z-30 flex max-h-[calc(100%-1.5rem)] -translate-y-1/2 flex-col gap-1 rounded-2xl p-1.5"
    >
      {inRouter ? <RouterNavSink navRef={navRef} /> : null}
      <button
        type="button"
        className="floor-rail-step"
        onClick={() => step(1)}
        disabled={curIdx < 0 || curIdx >= floors.length - 1}
        aria-label={copy.floorUp}
        title={`${copy.floorUp} (Alt+↑)`}
      >
        <ChevronUpIcon aria-hidden className="size-4" />
      </button>
      <div className="flex min-h-0 flex-col gap-0.5 overflow-y-auto" role="group" aria-label={project?.title || copy.floorSwitcherAria}>
        {topDown.map(({ f: floor, level }) => {
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
              title={floor.label}
              className={cn(
                'floor-rail-item relative flex h-10 min-h-10 shrink-0 items-center gap-2 rounded-xl pr-3 pl-2 text-xs whitespace-nowrap outline-none',
                'focus-visible:ring-3 focus-visible:ring-ring/60',
                active ? 'text-primary-foreground' : 'text-foreground',
              )}
            >
              {active ? (
                <motion.span
                  layoutId={reduced ? undefined : 'floor-rail-active'}
                  aria-hidden
                  className="absolute inset-0 rounded-xl bg-primary shadow-sm"
                  transition={{ type: 'spring', bounce: 0, duration: 0.35 }}
                />
              ) : null}
              <SlabIcon active={active} />
              <span className={cn('num relative w-6 text-left font-semibold', active ? 'opacity-80' : 'text-muted-foreground')}>
                L{level}
              </span>
              <span className="relative max-w-32 truncate font-medium">{floor.label}</span>
              {showCount ? (
                <span
                  data-floor-hits={count}
                  className={cn(
                    'num relative ml-auto rounded-full px-1.5 py-0.5 text-xs leading-none font-semibold',
                    active ? 'bg-primary-foreground/20 text-primary-foreground' : 'bg-info text-white',
                  )}
                >
                  {count}
                </span>
              ) : null}
            </button>
          )
        })}
      </div>
      <button
        type="button"
        className="floor-rail-step"
        onClick={() => step(-1)}
        disabled={curIdx <= 0}
        aria-label={copy.floorDown}
        title={`${copy.floorDown} (Alt+↓)`}
      >
        <ChevronDownIcon aria-hidden className="size-4" />
      </button>
      <div aria-hidden="true" className="mx-1 h-px shrink-0 bg-foreground/10" />
      <button
        type="button"
        data-cross-plan-toggle=""
        aria-pressed={crossPlanScope}
        onClick={toggleCrossPlan}
        title={copy.allFloors(floors.length)}
        className={cn(
          'flex h-10 min-h-10 shrink-0 items-center gap-2 rounded-xl px-2.5 text-xs font-medium whitespace-nowrap transition-colors outline-none',
          'focus-visible:ring-3 focus-visible:ring-ring/60',
          crossPlanScope ? 'bg-user text-primary-foreground shadow-xs hover:bg-user/90' : 'text-muted-foreground hover:bg-foreground/5 hover:text-foreground',
        )}
      >
        <LayersIcon aria-hidden="true" className="size-4 shrink-0" />
        <span>{copy.allFloors(floors.length)}</span>
      </button>
    </nav>
  )
}

/** A floor slab seen from above at an angle: the building-level metaphor in 18 px. */
function SlabIcon({ active }: { active: boolean }) {
  return (
    <svg aria-hidden viewBox="0 0 22 14" className="relative h-3.5 w-[22px] shrink-0">
      <path d="M11 1 21 6 11 11 1 6Z" fill="currentColor" opacity={active ? 0.95 : 0.18} />
      <path d="M1 6v2l10 5 10-5V6l-10 5Z" fill="currentColor" opacity={active ? 0.55 : 0.32} />
    </svg>
  )
}

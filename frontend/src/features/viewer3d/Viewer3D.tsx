import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { useNavigate } from 'react-router'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import {
  BoxIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  ImageUpIcon,
  InfoIcon,
  LayersIcon,
  RotateCcwIcon,
  Rotate3dIcon,
  SquareIcon,
  TagIcon,
  Undo2Icon,
  XIcon,
} from 'lucide-react'
import type { Box, Drawing } from '@/api/contract'
import { Button } from '@/components/ui/button'
import { Slider } from '@/components/ui/slider'
import { Spinner } from '@/components/ui/spinner'
import { Toggle } from '@/components/ui/toggle'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { t } from '@/i18n/strings'
import { useUi } from '@/i18n/uiLang'
import { isBoxRejected, isRoomIncluded } from '@/lib/answer'
import { fmtArea } from '@/lib/format'
import { cn } from '@/lib/utils'
import { myProjects } from '@/api/library'
import { readActiveProject, resolveProjectForDrawing } from '@/features/plan/badges'
import { actions, selectIsPending, useActiveTurn, useAppStore } from '@/store'
import { buildBuilding, type BuildingModel, type FloorEvidence, type FloorInput, type StackMode } from './buildingModel'
import { Engine, type CameraPreset } from './engine'
import { buildImageModel, type ImageMode, type ImageModel } from './imageModel'
import { DEFAULT_WALL_H, buildPlanModel, type PlanModel } from './planModel'
import { isRebuiltPlanSvg, parsePlanSvg } from './svgPlan'
import './viewer3d.css'

/** What is on screen: the open drawing, or an image the user dropped in (kept in the browser only). */
type Source = { kind: 'drawing' } | { kind: 'file'; name: string; url: string }

type Status =
  | { state: 'loading' }
  | { state: 'ready'; model: 'plan' | 'image' | 'building' }
  | { state: 'error'; message: string }

type AnyModel = PlanModel | ImageModel | BuildingModel
const isBuilding = (m: AnyModel | null): m is BuildingModel => !!m && 'floorIds' in m

/** An image without a known scale is shown 20 m wide: a house-sized sheet, so default wall heights look plausible. */
const ASSUMED_SHEET_M = 20

function hasWebgl(): boolean {
  try {
    const c = document.createElement('canvas')
    const gl = c.getContext('webgl2') ?? c.getContext('webgl')
    // Give the probe context back right away: browsers cap live contexts.
    gl?.getExtension('WEBGL_lose_context')?.loseContext()
    return !!gl
  } catch {
    return false
  }
}

function loadImage(url: string, cors: boolean): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    if (cors) img.crossOrigin = 'anonymous'
    img.decoding = 'async'
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error('image failed to load'))
    img.src = url
  })
}

/** Builds the plan model from plan.svg when the drawing has one with walls; otherwise null (use the image). */
/** One floor (or a single drawing): the CAD/IFC plan when there is one, else the image extruded as walls. */
async function floorModel(drawing: Drawing, signal: AbortSignal, stroke: number): Promise<PlanModel | ImageModel> {
  const plan = await tryPlan(drawing, signal)
  if (plan) return plan
  const img = await loadImage(drawing.images.rebuilt, true)
  const widthM = drawing.m_per_pct_x > 0 ? drawing.m_per_pct_x * 100 : ASSUMED_SHEET_M
  const m = buildImageModel(img, { widthM, minStroke: stroke })
  m.setMode('walls')
  return m
}

async function tryPlan(drawing: Drawing, signal: AbortSignal): Promise<PlanModel | null> {
  const url = drawing.images.rebuilt
  if (!/\.svg(\?|$)/i.test(url) && drawing.kind === 'raster') return null
  const res = await fetch(url, { signal })
  if (!res.ok) throw new Error(`plan.svg: HTTP ${res.status}`)
  const text = await res.text()
  if (!isRebuiltPlanSvg(text)) return null
  return buildPlanModel(drawing, parsePlanSvg(text))
}

export default function Viewer3D() {
  const { tu, lang } = useUi()
  const drawing = useAppStore((s) => s.session.drawing)
  const askLang = useAppStore((s) => s.session.lang)
  const pending = useAppStore(selectIsPending)
  const turn = useActiveTurn()
  const navigate = useNavigate()
  const reducedMotion = useReducedMotion() ?? false

  // The building this drawing belongs to (2+ floors), lowest floor first.
  const storeProject = useAppStore(readActiveProject)
  const [projRev, setProjRev] = useState(0)
  useEffect(() => myProjects.subscribe(() => setProjRev((r) => r + 1)), [])
  const project = useMemo(() => {
    void projRev
    return resolveProjectForDrawing(storeProject, drawing.id)
  }, [storeProject, drawing.id, projRev])
  const floorList = useMemo(
    () => (project && project.floors.length >= 2 ? [...project.floors].sort((a, b) => a.order - b.order) : null),
    [project],
  )
  const buildingKey = floorList ? `${project!.id}:${floorList.map((f) => f.drawing_id).join(',')}` : null
  const drawingRef = useRef(drawing)
  useLayoutEffect(() => {
    drawingRef.current = drawing
  }, [drawing])

  const hostRef = useRef<HTMLDivElement>(null)
  const engineRef = useRef<Engine | null>(null)
  const modelRef = useRef<AnyModel | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  // Checked once up front; an engine that still fails to start shows the same message.
  const [noWebgl, setNoWebgl] = useState(() => !hasWebgl())
  const [source, setSource] = useState<Source>({ kind: 'drawing' })
  const [status, setStatus] = useState<Status>({ state: 'loading' })
  const [wallH, setWallH] = useState(DEFAULT_WALL_H)
  const [labels, setLabels] = useState(true)
  const [spin, setSpin] = useState(false)
  const [preset, setPreset] = useState<CameraPreset>('persp')
  const [imageMode, setImageMode] = useState<ImageMode>('relief')
  const [stroke, setStroke] = useState(5)
  // The wall mask is rebuilt when the slider is released, not on every step of a drag.
  const [strokeLive, setStrokeLive] = useState(5)
  const [relief, setRelief] = useState(6)
  const [hovered, setHovered] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [fileError, setFileError] = useState<string | null>(null)
  // Building view: the whole exploded stack, or one floor in focus.
  const [stackMode, setStackMode] = useState<StackMode>('stack')
  const [floorHover, setFloorHover] = useState<string | null>(null)

  /** Go to a floor: focus it in 3D and make it the app's current drawing (chat and 2D follow). */
  const goFloor = useCallback(
    (id: string) => {
      setStackMode('floor')
      setFloorHover(null)
      if (id !== drawingRef.current.id) void navigate(`/d/${encodeURIComponent(id)}`)
    },
    [navigate],
  )
  const goFloorRef = useRef(goFloor)
  useLayoutEffect(() => {
    goFloorRef.current = goFloor
  }, [goFloor])
  const invalidate = () => engineRef.current?.invalidate()

  // ---- engine lifetime ----
  useEffect(() => {
    const host = hostRef.current
    if (!host || noWebgl) return
    let engine: Engine
    try {
      engine = new Engine(host)
    } catch {
      // Rare: a context that was available a moment ago (GPU blocklist, lost context). Report it after this effect.
      const id = requestAnimationFrame(() => setNoWebgl(true))
      return () => cancelAnimationFrame(id)
    }
    engineRef.current = engine
    // Dev-only handle for debugging and Playwright (frame state, springs).
    if (import.meta.env.DEV) (window as unknown as { __v3d?: Engine }).__v3d = engine
    return () => {
      engine.dispose()
      engineRef.current = null
    }
  }, [noWebgl])

  // ---- build the model for the current source ----
  useEffect(() => {
    const engine = engineRef.current
    if (!engine) return
    const ac = new AbortController()
    let model: AnyModel | null = null
    setStatus({ state: 'loading' })
    setSelected(null)
    setHovered(null)
    ;(async () => {
      try {
        const drawing = drawingRef.current
        if (source.kind === 'drawing' && floorList) {
          // Every floor of the building, fetched in parallel (cached floors switch instantly afterwards).
          const loaded = await Promise.all(
            floorList.map(async (f): Promise<FloorInput | null> => {
              try {
                const d = f.drawing_id === drawing.id ? drawing : await actions().warmDrawing(f.drawing_id)
                if (!d) return null
                return { id: f.drawing_id, label: f.label, drawing: d, model: await floorModel(d, ac.signal, stroke) }
              } catch {
                return null
              }
            }),
          )
          const inputs = loaded.filter((x): x is FloorInput => !!x)
          if (ac.signal.aborted) {
            for (const i of inputs) i.model.dispose()
            return
          }
          if (inputs.length >= 2) {
            const b = buildBuilding(inputs, {
              onFloorClick: (id) => goFloorRef.current(id),
              onFloorHover: (id) => setFloorHover(id),
              reducedMotion,
            })
            model = b
            modelRef.current = b
            setStackMode('stack')
            b.setMode('stack', drawing.id)
            engine.show(b, wallH, false, b.framing('stack', drawing.id), 24)
            setPreset('persp')
            setStatus({ state: 'ready', model: 'building' })
            return
          }
          for (const i of inputs) i.model.dispose()
        }
        if (source.kind === 'drawing') {
          model = await floorModel(drawing, ac.signal, stroke)
        } else {
          const img = await loadImage(source.url, false)
          model = buildImageModel(img, { widthM: ASSUMED_SHEET_M, minStroke: stroke })
        }
        if (ac.signal.aborted) return model.dispose()
        modelRef.current = model
        const isImage = !('rooms' in model)
        if (isImage) {
          const m = model as ImageModel
          // Images that came with the drawing are floor plans (raster uploads); dropped files start as relief.
          const mode: ImageMode = source.kind === 'drawing' ? 'walls' : 'relief'
          setImageMode(mode)
          m.setRelief(relief / 100)
          m.setMode(mode)
        }
        engine.show(model as PlanModel | ImageModel, wallH, true)
        setPreset('persp')
        setStatus({ state: 'ready', model: isImage ? 'image' : 'plan' })
      } catch (e) {
        if (ac.signal.aborted) return
        setStatus({ state: 'error', message: e instanceof Error ? e.message : String(e) })
      }
    })()
    return () => {
      ac.abort()
      if (model) {
        engineRef.current?.clear(model)
        model.dispose()
        if (modelRef.current === model) modelRef.current = null
      }
    }
    // Rebuild only when what is shown changes: the building (not the floor within it), a single drawing, or a file.
    // Sliders and the current floor update the live model below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buildingKey ?? drawing, source])

  // ---- building: mode and current floor drive the stack and the camera ----
  const framedRef = useRef('')
  useEffect(() => {
    const m = modelRef.current
    if (status.state !== 'ready' || !isBuilding(m)) return
    m.setMode(stackMode, drawing.id)
    const key = `${stackMode}:${stackMode === 'floor' ? drawing.id : ''}`
    if (framedRef.current !== key) {
      // The first framing comes from show(); after that every mode or floor change glides the camera there.
      if (framedRef.current) engineRef.current?.goTo(preset, m.framing(stackMode, drawing.id))
      framedRef.current = key
    }
    setSelected(null)
    invalidate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stackMode, drawing.id, status])
  useEffect(() => {
    if (status.state !== 'ready') framedRef.current = ''
  }, [status])
  useEffect(() => {
    const m = modelRef.current
    if (isBuilding(m)) m.setHover(stackMode === 'stack' ? floorHover : null)
    invalidate()
     
  }, [floorHover, stackMode, status])

  // ---- live settings ----
  useEffect(() => engineRef.current?.setWallHeight(wallH), [wallH])
  useEffect(() => engineRef.current?.setSpin(spin), [spin])
  useEffect(() => {
    const m = modelRef.current
    if (isBuilding(m)) m.setRoomLabels(labels)
    else if (m && 'setLabels' in m) m.setLabels(labels)
    invalidate()
     
  }, [labels, status])
  useEffect(() => {
    const m = modelRef.current
    if (m && !isBuilding(m) && 'setMode' in m) m.setMode(imageMode)
    invalidate()
  }, [imageMode, status])
  useEffect(() => {
    const m = modelRef.current
    if (m && 'setRelief' in m) m.setRelief(relief / 100)
    invalidate()
  }, [relief, status])
  useEffect(() => {
    const m = modelRef.current
    if (m && 'setMinStroke' in m) m.setMinStroke(stroke)
    invalidate()
  }, [stroke, status])

  // ---- evidence of the active answer ----
  const streaming = turn?.status === 'pending' && turn.stream ? turn.stream : undefined
  const ev = turn?.status === 'done' ? turn.response?.evidence : streaming?.evidence
  // Keyed on content: a streaming answer makes a new turn object per token, which must not rebuild the pins.
  const roomIds = !ev || !turn ? [] : ev.rooms.filter((r) => isRoomIncluded(r, turn.toggles)).map((r) => r.room_id)
  const roomKey = roomIds.join('|')
  const evidenceRooms = useStable(new Set(roomIds), roomKey)
  const pinBoxes = !ev || !turn ? [] : ev.boxes.filter((b) => !isBoxRejected(b.id, turn.toggles) && b.subject !== 'label')
  const pinKey = pinBoxes.map((b) => b.id).join('|')
  const pins: Box[] = useStable(pinBoxes, pinKey)

  // Per floor: a cross-floor answer tags each box and room with its floor's drawing id; others belong here.
  const roomFloors = !ev || !turn ? '' : ev.rooms.map((r) => (r as { drawing_id?: string }).drawing_id ?? '').join('|')
  const boxFloors = pinBoxes.map((b) => (b as { drawing_id?: string }).drawing_id ?? '').join('|')
  const perFloorNow = (() => {
    const per = new Map<string, FloorEvidence>()
    const scoped = /[^|]/.test(roomFloors) || /[^|]/.test(boxFloors)
    const slot = (fid: string | undefined) => {
      const id = fid || (scoped ? '' : drawing.id)
      if (!id) return null
      if (!per.has(id)) per.set(id, { rooms: new Set(), boxes: [] })
      return per.get(id)!
    }
    const rf = roomFloors ? roomFloors.split('|') : []
    roomIds.forEach((id) => {
      const i = ev?.rooms.findIndex((r) => r.room_id === id) ?? -1
      slot(rf[i])?.rooms.add(id)
    })
    for (const b of pins) slot((b as { drawing_id?: string }).drawing_id)?.boxes.push(b)
    return per
  })()
  const perFloor = useStable(perFloorNow, `${roomKey}#${pinKey}#${roomFloors}#${boxFloors}#${drawing.id}`)
  const floorCounts = useMemo(() => {
    const c = new Map<string, number>()
    for (const [id, e] of perFloor) c.set(id, e.boxes.length + e.rooms.size)
    return c
  }, [perFloor])

  useEffect(() => {
    const m = modelRef.current
    if (isBuilding(m)) {
      m.setEvidence(perFloor, hovered, selected)
      m.setCounts(floorCounts)
    } else if (m && 'setHighlight' in m) m.setHighlight({ evidence: evidenceRooms, hovered, selected })
    invalidate()
     
  }, [evidenceRooms, perFloor, floorCounts, hovered, selected, status, stackMode, drawing.id])
  useEffect(() => {
    const m = modelRef.current
    if (!m || isBuilding(m) || !('setPins' in m)) return
    m.setPins(pins)
    invalidate()
     
  }, [pins, status])

  // ---- picking: hover and click (a drag is not a click) ----
  const down = useRef<{ x: number; y: number } | null>(null)
  /** Rooms of the floor in focus only: faded floors let the ray through. */
  const pickRoom = (x: number, y: number) => {
    const m = modelRef.current
    if (isBuilding(m)) return engineRef.current?.pick(x, y, 'roomId', (o) => m.floorOf(o) === drawing.id) ?? null
    return engineRef.current?.pick(x, y, 'roomId') ?? null
  }
  const inStack = status.state === 'ready' && status.model === 'building' && stackMode === 'stack'
  const onPointerMove = (e: React.PointerEvent) => {
    if (e.pointerType !== 'mouse' || status.state !== 'ready') return
    if (inStack) {
      const id = engineRef.current?.pick(e.clientX, e.clientY, 'floorId') ?? null
      if (id !== floorHover) setFloorHover(id)
      return
    }
    if (status.model === 'image') return
    const id = pickRoom(e.clientX, e.clientY)
    if (id !== hovered) setHovered(id)
  }
  const onPointerUp = (e: React.PointerEvent) => {
    const d = down.current
    down.current = null
    if (!d || Math.hypot(e.clientX - d.x, e.clientY - d.y) > 5) return
    if (status.state !== 'ready' || status.model === 'image') return
    if (inStack) {
      const id = engineRef.current?.pick(e.clientX, e.clientY, 'floorId') ?? null
      if (id) goFloor(id)
      return
    }
    const id = pickRoom(e.clientX, e.clientY)
    setSelected((cur) => (cur === id ? null : id))
  }

  // ---- local images ----
  const openFile = (file: File | undefined) => {
    setFileError(null)
    if (!file) return
    if (!file.type.startsWith('image/')) {
      setFileError(tu('v3d_bad_file'))
      return
    }
    // Created once here; the effect below revokes it when the source changes or the view closes.
    setSource({ kind: 'file', name: file.name, url: URL.createObjectURL(file) })
  }
  useEffect(() => () => {
    if (source.kind === 'file') URL.revokeObjectURL(source.url)
  }, [source])
  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    openFile(e.dataTransfer.files?.[0])
  }

  const room = selected ? drawing.rooms.find((r) => r.room_id === selected) : undefined
  const unitLabel = room?.unit ? drawing.units.find((u) => u.id === room.unit)?.label ?? room.unit : null
  const isImage = status.state === 'ready' && status.model === 'image'
  const isBuildingView = status.state === 'ready' && status.model === 'building'
  // Plan-like: rooms, labels and the room card (one drawing, or the floor in focus of a building).
  const isPlan = (status.state === 'ready' && status.model === 'plan') || (isBuildingView && stackMode === 'floor')
  const showWalls = isPlan || (isImage && imageMode === 'walls')
  const floorIdx = floorList ? floorList.findIndex((f) => f.drawing_id === drawing.id) : -1
  const currentFloor = floorIdx >= 0 ? floorList![floorIdx] : null
  const stepTo = (delta: 1 | -1) => {
    if (!floorList || floorIdx < 0) return
    const next = floorList[floorIdx + delta]
    if (next) goFloor(next.drawing_id)
    else setStackMode('floor')
  }
  // Direction of the last floor change, so the title slides the way the building moved (derived state).
  const [floorNav, setFloorNav] = useState({ idx: floorIdx, dir: 1 as 1 | -1 })
  if (floorNav.idx !== floorIdx) setFloorNav({ idx: floorIdx, dir: floorIdx > floorNav.idx ? 1 : -1 })
  const direction = floorNav.idx !== floorIdx ? (floorIdx > floorNav.idx ? 1 : -1) : floorNav.dir
  const note = isPlan ? tu('v3d_note_cad') : isImage ? tu(imageMode === 'walls' ? 'v3d_note_walls' : 'v3d_note_relief') : null

  if (noWebgl)
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground" role="status">
        {tu('v3d_no_webgl')}
      </div>
    )

  return (
    <div
      className={cn('v3d-stage relative h-full w-full overflow-hidden', dragOver && 'is-drag')}
      role="region"
      aria-label={tu('v3d_region')}
      data-testid="viewer3d"
      data-v3d-state={status.state}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes('Files')) {
          e.preventDefault()
          setDragOver(true)
        }
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
    >
      <div
        ref={hostRef}
        className={cn('absolute inset-0', hovered && 'cursor-pointer')}
        onPointerDown={(e) => (down.current = { x: e.clientX, y: e.clientY })}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => {
          setHovered(null)
          setFloorHover(null)
        }}
      />

      {/* Where am I: building and floor, sliding in the direction the building moved */}
      {isBuildingView && project && currentFloor ? (
        <div className="pointer-events-none absolute top-3 left-1/2 z-10 -translate-x-1/2" aria-live="polite">
          <div className="v3d-glass flex h-10 items-center gap-2 overflow-hidden rounded-full px-4 text-sm" data-v3d-title="">
            <span className="max-w-48 truncate font-semibold tracking-tight">{project.title}</span>
            <span aria-hidden className="h-4 w-px bg-foreground/15" />
            <span className="relative grid">
              <AnimatePresence initial={false} mode="popLayout" custom={direction}>
                <motion.span
                  key={stackMode === 'stack' ? 'all' : currentFloor.drawing_id}
                  custom={direction}
                  initial={reducedMotion ? { opacity: 0 } : { opacity: 0, y: direction * -14, filter: 'blur(4px)' }}
                  animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
                  exit={reducedMotion ? { opacity: 0 } : { opacity: 0, y: direction * 14, filter: 'blur(4px)' }}
                  transition={{ type: 'spring', bounce: 0, duration: 0.4 }}
                  className="col-start-1 row-start-1 whitespace-nowrap text-muted-foreground"
                >
                  {stackMode === 'stack' ? (
                    tu('v3d_all_floors', { n: floorList!.length })
                  ) : (
                    <>
                      <span className="num mr-1.5 font-semibold text-foreground">L{floorIdx + 1}</span>
                      {currentFloor.label}
                    </>
                  )}
                </motion.span>
              </AnimatePresence>
            </span>
          </div>
        </div>
      ) : null}

      {/* Floor stepper: up, whole building, down (right edge, vertically centred) */}
      {isBuildingView && floorList ? (
        <div
          className="v3d-glass absolute top-1/2 right-3 z-10 flex -translate-y-1/2 flex-col overflow-hidden rounded-2xl"
          role="group"
          aria-label={tu('v3d_floors')}
          data-v3d-stepper=""
        >
          <button
            type="button"
            className="v3d-step"
            onClick={() => stepTo(1)}
            disabled={stackMode === 'floor' && floorIdx >= floorList.length - 1}
            aria-label={tu('v3d_floor_up')}
            title={tu('v3d_floor_up')}
          >
            <ChevronUpIcon className="size-5" />
          </button>
          <button
            type="button"
            className={cn('v3d-step', stackMode === 'stack' && 'is-on')}
            onClick={() => setStackMode((m) => (m === 'stack' ? 'floor' : 'stack'))}
            aria-pressed={stackMode === 'stack'}
            aria-label={tu('v3d_all_floors', { n: floorList.length })}
            title={tu('v3d_all_floors', { n: floorList.length })}
            data-v3d-stack-toggle=""
          >
            <LayersIcon className="size-5" />
          </button>
          <button
            type="button"
            className="v3d-step"
            onClick={() => stepTo(-1)}
            disabled={stackMode === 'floor' && floorIdx <= 0}
            aria-label={tu('v3d_floor_down')}
            title={tu('v3d_floor_down')}
          >
            <ChevronDownIcon className="size-5" />
          </button>
        </div>
      ) : null}

      {/* Controls: top left */}
      <div className="v3d-panel absolute top-3 left-3 flex max-w-[calc(100%-1.5rem)] flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <ToggleGroup
            type="single"
            variant="outline"
            spacing={0}
            value={preset}
            onValueChange={(v) => {
              if (!v) return
              setPreset(v as CameraPreset)
              const m = modelRef.current
              engineRef.current?.goTo(v as CameraPreset, isBuilding(m) ? m.framing(stackMode, drawing.id) : undefined)
            }}
            aria-label={tu('view_toggle')}
            className="bg-background"
          >
            <ToggleGroupItem value="persp" aria-label={tu('v3d_persp')} className="gap-1.5">
              <BoxIcon data-icon="inline-start" />
              <span className="max-sm:sr-only">{tu('v3d_persp')}</span>
            </ToggleGroupItem>
            <ToggleGroupItem value="top" aria-label={tu('v3d_top')} className="gap-1.5">
              <SquareIcon data-icon="inline-start" />
              <span className="max-sm:sr-only">{tu('v3d_top')}</span>
            </ToggleGroupItem>
          </ToggleGroup>
          <Button
            variant="outline"
            size="icon"
            className="bg-background"
            aria-label={tu('v3d_reset')}
            title={tu('v3d_reset')}
            onClick={() => {
              setPreset('persp')
              const m = modelRef.current
              engineRef.current?.goTo('persp', isBuilding(m) ? m.framing(stackMode, drawing.id) : undefined)
            }}
          >
            <RotateCcwIcon />
          </Button>
          <Toggle variant="outline" className="bg-background" pressed={spin} onPressedChange={setSpin} aria-label={tu('v3d_spin')} title={tu('v3d_spin')}>
            <Rotate3dIcon />
          </Toggle>
          {isPlan || isBuildingView ? (
            <Toggle variant="outline" className="bg-background" pressed={labels} onPressedChange={setLabels} aria-label={tu('v3d_labels')} title={tu('v3d_labels')}>
              <TagIcon />
            </Toggle>
          ) : null}
        </div>

        {status.state === 'ready' && !inStack ? (
          <div className="flex w-64 max-w-full flex-col gap-3 rounded-lg border bg-background/95 p-3 shadow-sm backdrop-blur">
            {isImage ? (
              <ToggleGroup
                type="single"
                variant="outline"
                spacing={0}
                value={imageMode}
                onValueChange={(v) => v && setImageMode(v as ImageMode)}
                aria-label={tu('v3d_mode')}
                className="w-full"
              >
                <ToggleGroupItem value="relief" className="flex-1">
                  {tu('v3d_relief')}
                </ToggleGroupItem>
                <ToggleGroupItem value="walls" className="flex-1">
                  {tu('v3d_walls')}
                </ToggleGroupItem>
              </ToggleGroup>
            ) : null}
            {showWalls ? (
              <SliderRow label={tu('v3d_wall_h')} value={`${wallH.toFixed(1)} m`}>
                <Slider min={0.2} max={3.2} step={0.1} value={[wallH]} onValueChange={([v]) => setWallH(v)} thumbLabel={tu('v3d_wall_h')} />
              </SliderRow>
            ) : null}
            {isImage && imageMode === 'walls' ? (
              <SliderRow label={tu('v3d_stroke')} value={`≥ ${strokeLive} px`}>
                <Slider
                  min={2}
                  max={12}
                  step={1}
                  value={[strokeLive]}
                  onValueChange={([v]) => setStrokeLive(v)}
                  onValueCommit={([v]) => setStroke(v)}
                  thumbLabel={tu('v3d_stroke')}
                />
              </SliderRow>
            ) : null}
            {isImage ? (
              <SliderRow label={tu('v3d_depth')} value={`${relief} cm`}>
                <Slider min={0} max={30} step={1} value={[relief]} onValueChange={([v]) => setRelief(v)} thumbLabel={tu('v3d_depth')} />
              </SliderRow>
            ) : null}
            {note ? (
              <p className="flex gap-1.5 text-xs leading-snug text-muted-foreground">
                <InfoIcon className="mt-px size-3.5 shrink-0" aria-hidden />
                <span>{note}</span>
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      {/* Image source: top right */}
      <div className="absolute top-3 right-3 flex flex-col items-end gap-2">
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          className="sr-only"
          tabIndex={-1}
          aria-hidden
          onChange={(e) => {
            openFile(e.target.files?.[0])
            e.target.value = ''
          }}
        />
        {source.kind === 'file' ? (
          <Button variant="outline" className="bg-background" onClick={() => setSource({ kind: 'drawing' })}>
            <Undo2Icon data-icon="inline-start" />
            {tu('v3d_back')}
          </Button>
        ) : null}
        <Button variant="outline" className="bg-background" onClick={() => fileRef.current?.click()} title={tu('v3d_open_image_hint')}>
          <ImageUpIcon data-icon="inline-start" />
          <span className="max-sm:sr-only">{tu('v3d_open_image')}</span>
        </Button>
        {source.kind === 'file' ? <span className="max-w-56 truncate rounded bg-background/90 px-2 py-0.5 text-xs text-muted-foreground">{source.name}</span> : null}
        {fileError ? (
          <p className="max-w-64 rounded-md border border-destructive/30 bg-destructive-surface px-2 py-1 text-xs text-destructive" role="alert">
            {fileError}
          </p>
        ) : null}
      </div>

      {/* Evidence count and hint: bottom left */}
      <div className="pointer-events-none absolute bottom-3 left-3 flex flex-col items-start gap-1.5">
        {isPlan && (pins.length > 0 || evidenceRooms.size > 0) ? (
          <span className="v3d-chip is-evidence">{tu('v3d_evidence', { n: pins.length || evidenceRooms.size })}</span>
        ) : null}
        {status.state === 'ready' ? (
          <span className="v3d-chip max-md:hidden">{inStack ? tu('v3d_hint_stack') : tu('v3d_hint')}</span>
        ) : null}
      </div>

      {/* Selected room: bottom right */}
      {room && isPlan ? (
        <div className="absolute right-3 bottom-3 w-64 max-w-[calc(100%-1.5rem)] rounded-lg border bg-background/95 p-3 shadow-md backdrop-blur" role="dialog" aria-label={room.name || room.num}>
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">{room.name || room.num}</p>
              <p className="num text-xs text-muted-foreground">
                {room.num}
                {unitLabel ? ` · ${unitLabel}` : ''}
              </p>
            </div>
            <Button variant="ghost" size="icon" className="-mt-1 -mr-1 size-8" aria-label={tu('v3d_close')} onClick={() => setSelected(null)}>
              <XIcon />
            </Button>
          </div>
          {room.area_m2 != null ? <p className="num mt-2 text-2xl font-semibold tracking-tight">{fmtArea(room.area_m2, lang)} m²</p> : null}
          <Button
            className="mt-3 w-full"
            size="sm"
            disabled={pending}
            onClick={() => void actions().ask(t(askLang, 'whatif_question_room', { room: room.num }), { scope: { kind: 'room', room_id: room.room_id } })}
          >
            {tu('v3d_ask_area')}
          </Button>
        </div>
      ) : null}

      {status.state === 'loading' ? (
        <div className="absolute inset-0 flex items-center justify-center" role="status">
          <span className="flex items-center gap-2 rounded-full border bg-background px-3 py-1.5 text-sm shadow-sm">
            <Spinner />
            {tu('v3d_loading')}
          </span>
        </div>
      ) : null}
      {status.state === 'error' ? (
        <div className="absolute inset-0 flex items-center justify-center p-6" role="alert">
          <div className="max-w-sm rounded-lg border bg-background p-4 text-center shadow-sm">
            <p className="text-sm font-medium">{tu('v3d_error')}</p>
            <p className="mt-1 text-xs break-words text-muted-foreground">{status.message}</p>
          </div>
        </div>
      ) : null}
      {dragOver ? (
        <div className="pointer-events-none absolute inset-3 flex items-center justify-center rounded-xl border-2 border-dashed border-user bg-user-surface/60 text-sm font-medium text-user">
          {tu('v3d_drop')}
        </div>
      ) : null}
    </div>
  )
}

/** The same object for as long as `key` is unchanged: content-keyed identity without a hand-written memo. */
function useStable<T>(value: T, key: string): T {
  const [held, setHeld] = useState({ key, value })
  if (held.key !== key) {
    setHeld({ key, value })
    return value
  }
  return held.value
}

function SliderRow({ label, value, children }: { label: string; value: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="num tabular-nums">{value}</span>
      </div>
      {children}
    </div>
  )
}

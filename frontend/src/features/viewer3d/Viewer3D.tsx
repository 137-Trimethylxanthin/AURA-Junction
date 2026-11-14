import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { BoxIcon, ImageUpIcon, InfoIcon, RotateCcwIcon, Rotate3dIcon, SquareIcon, TagIcon, Undo2Icon, XIcon } from 'lucide-react'
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
import { actions, selectIsPending, useActiveTurn, useAppStore } from '@/store'
import { Engine, type CameraPreset } from './engine'
import { buildImageModel, type ImageMode, type ImageModel } from './imageModel'
import { DEFAULT_WALL_H, buildPlanModel, type PlanModel } from './planModel'
import { isRebuiltPlanSvg, parsePlanSvg } from './svgPlan'
import './viewer3d.css'

/** What is on screen: the open drawing, or an image the user dropped in (kept in the browser only). */
type Source = { kind: 'drawing' } | { kind: 'file'; name: string; url: string }

type Status = { state: 'loading' } | { state: 'ready'; model: 'plan' | 'image' } | { state: 'error'; message: string }

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

  const hostRef = useRef<HTMLDivElement>(null)
  const engineRef = useRef<Engine | null>(null)
  const modelRef = useRef<PlanModel | ImageModel | null>(null)
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
    let model: PlanModel | ImageModel | null = null
    setStatus({ state: 'loading' })
    setSelected(null)
    setHovered(null)
    ;(async () => {
      try {
        if (source.kind === 'drawing') {
          model = await tryPlan(drawing, ac.signal)
          if (!model) {
            const img = await loadImage(drawing.images.rebuilt, true)
            const widthM = drawing.m_per_pct_x > 0 ? drawing.m_per_pct_x * 100 : ASSUMED_SHEET_M
            model = buildImageModel(img, { widthM, minStroke: stroke })
          }
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
        engine.show(model, wallH, true)
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
    // Rebuild only when what is shown changes; sliders update the live model below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawing, source])

  // ---- live settings ----
  const invalidate = () => engineRef.current?.invalidate()
  useEffect(() => engineRef.current?.setWallHeight(wallH), [wallH])
  useEffect(() => engineRef.current?.setSpin(spin), [spin])
  useEffect(() => {
    const m = modelRef.current
    if (m && 'setLabels' in m) m.setLabels(labels)
    invalidate()
  }, [labels, status])
  useEffect(() => {
    const m = modelRef.current
    if (m && 'setMode' in m) m.setMode(imageMode)
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
  const evidenceRooms = useMemo(() => new Set(roomKey ? roomKey.split('|') : []), [roomKey])
  const pinBoxes = !ev || !turn ? [] : ev.boxes.filter((b) => !isBoxRejected(b.id, turn.toggles) && b.subject !== 'label')
  const pinKey = pinBoxes.map((b) => b.id).join('|')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const pins: Box[] = useMemo(() => pinBoxes, [pinKey])

  useEffect(() => {
    const m = modelRef.current
    if (!m || !('setHighlight' in m)) return
    m.setHighlight({ evidence: evidenceRooms, hovered, selected })
    invalidate()
  }, [evidenceRooms, hovered, selected, status])
  useEffect(() => {
    const m = modelRef.current
    if (!m || !('setPins' in m)) return
    m.setPins(pins)
    invalidate()
  }, [pins, status])

  // ---- picking: hover and click (a drag is not a click) ----
  const down = useRef<{ x: number; y: number } | null>(null)
  const onPointerMove = (e: React.PointerEvent) => {
    if (e.pointerType !== 'mouse' || status.state !== 'ready' || status.model !== 'plan') return
    const id = engineRef.current?.pick(e.clientX, e.clientY, 'roomId') ?? null
    if (id !== hovered) setHovered(id)
  }
  const onPointerUp = (e: React.PointerEvent) => {
    const d = down.current
    down.current = null
    if (!d || Math.hypot(e.clientX - d.x, e.clientY - d.y) > 5) return
    if (status.state !== 'ready' || status.model !== 'plan') return
    const id = engineRef.current?.pick(e.clientX, e.clientY, 'roomId') ?? null
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
  const isPlan = status.state === 'ready' && status.model === 'plan'
  const showWalls = isPlan || (isImage && imageMode === 'walls')
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
        onPointerLeave={() => setHovered(null)}
      />

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
              engineRef.current?.goTo(v as CameraPreset)
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
              engineRef.current?.goTo('persp')
            }}
          >
            <RotateCcwIcon />
          </Button>
          <Toggle variant="outline" className="bg-background" pressed={spin} onPressedChange={setSpin} aria-label={tu('v3d_spin')} title={tu('v3d_spin')}>
            <Rotate3dIcon />
          </Toggle>
          {isPlan ? (
            <Toggle variant="outline" className="bg-background" pressed={labels} onPressedChange={setLabels} aria-label={tu('v3d_labels')} title={tu('v3d_labels')}>
              <TagIcon />
            </Toggle>
          ) : null}
        </div>

        {status.state === 'ready' ? (
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
        {status.state === 'ready' ? <span className="v3d-chip max-md:hidden">{tu('v3d_hint')}</span> : null}
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

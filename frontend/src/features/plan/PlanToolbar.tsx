import { useEffect } from 'react'
import {
  BoxIcon,
  CircleHelpIcon,
  Columns2Icon,
  Loader2Icon,
  MapIcon,
  MinusIcon,
  MousePointer2Icon,
  PlusIcon,
  RefreshCwIcon,
  RulerIcon,
  SquareDashedIcon,
} from 'lucide-react'
import { useUi } from '@/i18n/uiLang'
import type { UiKey } from '@/i18n/strings'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Slider } from '@/components/ui/slider'
import { Toggle } from '@/components/ui/toggle'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { has3dModel } from '@/features/viewer3d/available'
import { ZOOM_MAX, ZOOM_MIN, actions, selectZoom, useAppStore, useAppStoreShallow, useSource, type PlanView, type Tool } from '@/store'

const TOOLS: { id: Tool; label: UiKey; hint: UiKey; Icon: typeof MousePointer2Icon }[] = [
  { id: 'select', label: 'tool_select', hint: 'tool_select_hint', Icon: MousePointer2Icon },
  { id: 'mark', label: 'tool_mark', hint: 'tool_mark_hint', Icon: SquareDashedIcon },
  { id: 'whatif', label: 'tool_whatif', hint: 'tool_whatif_hint', Icon: RulerIcon },
]

function pickTool(next: string) {
  if (!next) return // re-clicking the active item emits ''
  const a = actions()
  if (a.ui.tool === 'whatif' && next !== 'whatif') a.cancelWhatIf()
  a.setTool(next as Tool)
}

export function PlanToolbar() {
  const tool = useAppStore((s) => s.ui.tool)
  const compare = useAppStoreShallow((s) => s.ui.compare)
  const hasOriginal = useAppStore((s) => !!s.session.drawing.images.original)
  // What-if needs rooms with a resizable rectangle, and is hidden only when server answerer is pure 'agent'.
  const canWhatIf = useAppStore((s) => s.session.drawing.rooms.some((r) => !!r.rect))
  const answererMode = useAppStore((s) => s.session.answererMode)
  const showWhatIf = canWhatIf && answererMode !== 'agent'
  const zoom = useAppStore(selectZoom)
  const has3d = useAppStore((s) => has3dModel(s.session.drawing))
  const view = useAppStore((s) => (has3dModel(s.session.drawing) ? s.ui.view : '2d'))
  const { tu } = useUi()

  useEffect(() => {
    if (tool === 'whatif' && !showWhatIf) actions().cancelWhatIf()
  }, [tool, showWhatIf])

  if (view === '3d')
    return (
      <div className="@container flex w-full min-w-0 flex-wrap items-center gap-2" role="toolbar" aria-label={tu('plan_tools')}>
        <ViewToggle view={view} has3d={has3d} />
        <div className="ml-auto flex items-center gap-2">
          <SourceBadge />
        </div>
      </div>
    )

  return (
    <div className="@container flex w-full min-w-0 flex-wrap items-center gap-2" role="toolbar" aria-label={tu('plan_tools')}>
      <ViewToggle view={view} has3d={has3d} />
      <ToggleGroup
        type="single"
        variant="outline"
        spacing={0}
        value={tool}
        onValueChange={pickTool}
        aria-label={tu('tool')}
      >
        {TOOLS.filter((x) => x.id !== 'whatif' || showWhatIf).map(({ id, label, hint, Icon }) => (
          <ToggleGroupItem
            key={id}
            value={id}
            title={tu(hint)}
            aria-label={tu(label)}
            className={cn(
              // One shape for all three. Select is neutral; Mark and What-if are user input → user colour.
              'gap-1.5 data-[state=on]:text-primary-foreground hover:data-[state=on]:text-primary-foreground',
              id === 'select'
                ? 'data-[state=on]:border-primary data-[state=on]:bg-primary hover:data-[state=on]:bg-primary/90'
                : 'data-[state=on]:border-user data-[state=on]:bg-user hover:data-[state=on]:bg-user/90',
            )}
          >
            <Icon data-icon="inline-start" />
            <span className={compare.on ? '@max-[1040px]:sr-only' : '@max-[520px]:sr-only'}>{tu(label)}</span>
          </ToggleGroupItem>
        ))}
      </ToggleGroup>

      {hasOriginal ? (
        <div className="flex flex-wrap items-center gap-2">
          <Toggle
            variant="outline"
            pressed={compare.on}
            onPressedChange={(on) => actions().setCompare({ on })}
            aria-label={tu('compare')}
            title={tu('compare')}
            className="gap-1.5"
          >
            <Columns2Icon data-icon="inline-start" />
            {/* Icon-only while on: the Swipe/Blend group next to it already says what's active. */}
            {/* Icon-only on narrow toolbars too, so the 2D/3D switch still fits on one row (portrait tablets). */}
            <span className={cn(compare.on ? 'sr-only' : '@max-[860px]:sr-only')}>{tu('compare')}</span>
          </Toggle>
          {compare.on ? (
            <>
              <ToggleGroup
                type="single"
                variant="outline"
                spacing={0}
                value={compare.mode}
                onValueChange={(m) => m && actions().setCompare({ mode: m as 'swipe' | 'blend' })}
                aria-label={tu('compare_mode')}
              >
                <ToggleGroupItem value="swipe">{tu('swipe')}</ToggleGroupItem>
                <ToggleGroupItem value="blend">{tu('blend')}</ToggleGroupItem>
              </ToggleGroup>
              {compare.mode === 'blend' ? (
                <div className="flex h-10 items-center gap-2 rounded-lg border px-2.5">
                  <span className="text-xs text-muted-foreground @max-[1040px]:sr-only" id="plan-blend-label">
                    {tu('tb_original')}
                  </span>
                  <Slider
                    className="w-20"
                    min={0}
                    max={100}
                    step={1}
                    value={[compare.opacity]}
                    onValueChange={([v]) => actions().setCompare({ opacity: v })}
                    thumbLabel={tu('original_opacity')}
                  />
                  <span className="num w-9 text-right text-xs">{compare.opacity}%</span>
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}

      <div className="ml-auto flex flex-wrap items-center gap-2">
        <SourceBadge />
        <div className="flex items-center rounded-lg border" role="group" aria-label={tu('tb_zoom')}>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label={tu('zoom_out')}
                disabled={zoom <= ZOOM_MIN}
                onClick={() => actions().zoomOut()}
                className="rounded-r-none"
              >
                <MinusIcon />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{tu('zoom_out')}</TooltipContent>
          </Tooltip>
          <button
            type="button"
            disabled={zoom === ZOOM_MIN}
            onClick={() => actions().resetZoom()}
            title={zoom === ZOOM_MIN ? undefined : tu('zoom_reset')}
            aria-live="polite"
            aria-label={zoom === ZOOM_MIN ? tu('tb_zoom_level', { n: zoom }) : `${tu('tb_zoom_level', { n: zoom })} — ${tu('zoom_reset')}`}
            className="num flex h-10 min-w-12 items-center justify-center px-1 text-center text-xs tabular-nums transition-colors hover:bg-muted disabled:pointer-events-none"
            data-zoom-readout=""
          >
            {zoom}%
          </button>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label={tu('zoom_in')}
                disabled={zoom >= ZOOM_MAX}
                onClick={() => actions().zoomIn()}
                className="rounded-l-none"
              >
                <PlusIcon />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{tu('zoom_in')}</TooltipContent>
          </Tooltip>
        </div>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              size="icon"
              aria-label={tu('shortcuts_btn')}
              onClick={() => actions().setShortcutsOpen(true)}
              data-shortcuts-trigger=""
            >
              <CircleHelpIcon />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{tu('shortcuts_btn')}</TooltipContent>
        </Tooltip>
      </div>
    </div>
  )
}

/** Flat drawing or 3D model of it. */
function ViewToggle({ view, has3d }: { view: PlanView; has3d: boolean }) {
  const { tu } = useUi()
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      spacing={0}
      value={view}
      onValueChange={(v) => v && actions().setView(v as PlanView)}
      aria-label={tu('view_toggle')}
      // a disabled button takes no pointer events: the reason is on the group, where the hover lands
      title={has3d ? undefined : tu('view_3d_unavailable')}
    >
      <ToggleGroupItem value="2d" aria-label={tu('view_2d')} className="gap-1.5">
        <MapIcon data-icon="inline-start" />
        <span className="@max-[900px]:sr-only">{tu('view_2d')}</span>
      </ToggleGroupItem>
      <ToggleGroupItem
        value="3d"
        aria-label={tu('view_3d')}
        disabled={!has3d}
        className="gap-1.5 data-[state=on]:border-primary data-[state=on]:bg-primary data-[state=on]:text-primary-foreground hover:data-[state=on]:bg-primary/90 hover:data-[state=on]:text-primary-foreground"
      >
        <BoxIcon data-icon="inline-start" />
        <span className="@max-[900px]:sr-only">{tu('view_3d')}</span>
      </ToggleGroupItem>
    </ToggleGroup>
  )
}

/** Where answers come from: bundled data, the live API, or a backend that is still waking. */
export function SourceBadge() {
  const src = useSource()
  const { tu } = useUi()
  if (src.state === 'waking')
    return (
      <Badge variant="outline" className="h-7 gap-1.5 text-muted-foreground" role="status">
        <Loader2Icon className="animate-spin" />
        {tu('src_waking')}
      </Badge>
    )
  if (src.kind === 'api')
    return (
      <Badge variant="outline" className="h-7 gap-1.5" role="status">
        <span aria-hidden className="size-1.5 rounded-full bg-foreground" />
        {tu('src_live')}
      </Badge>
    )
  const failed = src.reason === 'health_failed' || src.reason === 'fell_back'
  return (
    <div className="flex items-center gap-1">
      {failed ? (
        <Badge
          variant="outline"
          className="h-7 gap-1.5 border-warning/40 bg-warning-surface text-warning"
          role="status"
          title={tu('src_offline_hint')}
          data-source-offline
        >
          <span aria-hidden className="size-1.5 rounded-full bg-warning" />
          {tu('src_offline')}
        </Badge>
      ) : (
        <Badge variant="secondary" className="h-7" role="status" title={tu('src_local_hint')}>
          {tu('src_local')}
        </Badge>
      )}
      {failed ? (
        <Button variant="ghost" size="sm" className="h-10" onClick={() => void actions().retrySource()}>
          <RefreshCwIcon data-icon="inline-start" />
          <span className="@max-[1040px]:sr-only">{tu('src_reconnect')}</span>
        </Button>
      ) : null}
    </div>
  )
}

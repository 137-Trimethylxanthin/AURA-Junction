import { Columns2Icon, Loader2Icon, MinusIcon, MousePointer2Icon, PlusIcon, RefreshCwIcon, RulerIcon, SquareDashedIcon } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Slider } from '@/components/ui/slider'
import { Toggle } from '@/components/ui/toggle'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { ZOOM_STEPS, actions, selectZoom, useAppStore, useAppStoreShallow, useSource, type Tool } from '@/store'

const TOOLS: { id: Tool; label: string; hint: string; Icon: typeof MousePointer2Icon }[] = [
  { id: 'select', label: 'Select', hint: 'Click a box to find it; click again to reject it', Icon: MousePointer2Icon },
  { id: 'mark', label: 'Mark area', hint: 'Drag a rectangle on the plan to scope your question', Icon: SquareDashedIcon },
  { id: 'whatif', label: 'What-if sizes', hint: 'Pick a room to change its sizes', Icon: RulerIcon },
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
  // What-if needs rooms with a resizable rectangle; drawings without any (IFC, many uploads) do not offer it.
  const canWhatIf = useAppStore((s) => s.session.drawing.rooms.some((r) => !!r.rect))
  const zoom = useAppStore(selectZoom)
  const zoomIndex = useAppStore((s) => s.ui.zoomIndex)

  return (
    <div className="@container flex w-full min-w-0 flex-wrap items-center gap-2" role="toolbar" aria-label="Plan tools">
      <ToggleGroup
        type="single"
        variant="outline"
        spacing={0}
        value={tool}
        onValueChange={pickTool}
        aria-label="Tool"
      >
        {TOOLS.filter((x) => x.id !== 'whatif' || canWhatIf).map(({ id, label, hint, Icon }) => (
          <ToggleGroupItem
            key={id}
            value={id}
            title={hint}
            aria-label={label}
            className={cn(
              // One shape for all three. Select is neutral; Mark and What-if are user input → user colour.
              'gap-1.5 data-[state=on]:text-primary-foreground hover:data-[state=on]:text-primary-foreground',
              id === 'select'
                ? 'data-[state=on]:border-primary data-[state=on]:bg-primary hover:data-[state=on]:bg-primary/90'
                : 'data-[state=on]:border-user data-[state=on]:bg-user hover:data-[state=on]:bg-user/90',
            )}
          >
            <Icon data-icon="inline-start" />
            <span className="@max-[520px]:sr-only">{label}</span>
          </ToggleGroupItem>
        ))}
      </ToggleGroup>

      {hasOriginal ? (
        <div className="flex flex-wrap items-center gap-2">
          <Toggle
            variant="outline"
            pressed={compare.on}
            onPressedChange={(on) => actions().setCompare({ on })}
            aria-label="Compare with original"
            className="gap-1.5"
          >
            <Columns2Icon data-icon="inline-start" />
            {/* Icon-only while on: the Swipe/Blend group next to it already says what's active. */}
            <span className={cn(compare.on ? 'sr-only' : '@max-[520px]:sr-only')}>Compare with original</span>
          </Toggle>
          {compare.on ? (
            <>
              <ToggleGroup
                type="single"
                variant="outline"
                spacing={0}
                value={compare.mode}
                onValueChange={(m) => m && actions().setCompare({ mode: m as 'swipe' | 'blend' })}
                aria-label="Compare mode"
              >
                <ToggleGroupItem value="swipe">Swipe</ToggleGroupItem>
                <ToggleGroupItem value="blend">Blend</ToggleGroupItem>
              </ToggleGroup>
              {compare.mode === 'blend' ? (
                <div className="flex h-10 items-center gap-2 rounded-lg border px-2.5">
                  <span className="text-xs text-muted-foreground @max-[1040px]:sr-only" id="plan-blend-label">
                    Original
                  </span>
                  <Slider
                    className="w-20"
                    min={0}
                    max={100}
                    step={1}
                    value={[compare.opacity]}
                    onValueChange={([v]) => actions().setCompare({ opacity: v })}
                    thumbLabel="Original opacity"
                  />
                  <span className="num w-9 text-right text-xs">{compare.opacity}%</span>
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}

      <div className="ml-auto flex items-center gap-2">
        <SourceBadge />
        <div className="flex items-center rounded-lg border" role="group" aria-label="Zoom">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Zoom out"
                disabled={zoomIndex <= 0}
                onClick={() => actions().zoomOut()}
                className="rounded-r-none"
              >
                <MinusIcon />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Zoom out</TooltipContent>
          </Tooltip>
          <span className="num w-12 text-center text-xs tabular-nums" aria-live="polite" aria-label={`Zoom ${zoom} percent`}>
            {zoom}%
          </span>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Zoom in"
                disabled={zoomIndex >= ZOOM_STEPS.length - 1}
                onClick={() => actions().zoomIn()}
                className="rounded-l-none"
              >
                <PlusIcon />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Zoom in</TooltipContent>
          </Tooltip>
        </div>
      </div>
    </div>
  )
}

/** Where answers come from: bundled data, the live API, or a backend that is still waking. */
export function SourceBadge() {
  const src = useSource()
  if (src.state === 'waking')
    return (
      <Badge variant="outline" className="h-7 gap-1.5 text-muted-foreground" role="status">
        <Loader2Icon className="animate-spin" />
        Waking backend…
      </Badge>
    )
  if (src.kind === 'api')
    return (
      <Badge variant="outline" className="h-7 gap-1.5" role="status">
        <span aria-hidden className="size-1.5 rounded-full bg-foreground" />
        Live API
      </Badge>
    )
  const failed = src.reason === 'health_failed' || src.reason === 'fell_back'
  return (
    <div className="flex items-center gap-1">
      <Badge variant="secondary" className="h-7" role="status" title="Answers are computed in the browser from the bundled drawing data">
        Local data
      </Badge>
      {failed ? (
        <Button variant="ghost" size="sm" className="h-10" onClick={() => void actions().retrySource()}>
          <RefreshCwIcon data-icon="inline-start" />
          Retry API
        </Button>
      ) : null}
    </div>
  )
}

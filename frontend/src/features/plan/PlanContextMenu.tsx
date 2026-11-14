import { toast } from 'sonner'
import {
  Columns2Icon,
  LinkIcon,
  MessageCircleQuestionIcon,
  RulerIcon,
  SquareDashedIcon,
  SquareIcon,
} from 'lucide-react'
import {
  ContextMenuContent,
  ContextMenuGroup,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
} from '@/components/ui/context-menu'
import { fmtArea } from '@/lib/format'
import { nearestRoom } from '@/lib/geometry'
import { actions, useAppStore } from '@/store'
import { nextLabel } from './MarkTool'
import { swipeSplit } from './shared'
import { roomTitle } from './shared'

const SPOT = 4

/** Menu for a point on the plan (in % of the stage). Three groups, no shortcuts, unavailable items hidden. */
export function PlanContextMenu({
  point,
  onCloseAutoFocus,
  onFocusOutside,
}: {
  point: [number, number] | null
  onCloseAutoFocus?: (e: Event) => void
  onFocusOutside?: (e: Event) => void
}) {
  const rooms = useAppStore((s) => s.session.drawing.rooms)
  const lang = useAppStore((s) => s.session.lang)
  const hasOriginal = useAppStore((s) => !!s.session.drawing.images.original)
  const [x, y] = point ?? [50, 50]
  const room = point ? nearestRoom(rooms, x, y, 6) : null

  const askSpot = () => {
    const a = actions()
    const label = nextLabel('Spot')
    a.addMark({ x: x - SPOT / 2, y: y - SPOT / 2, w: SPOT, h: SPOT }, label)
    toast(`${label} attached`, { description: 'Your next question is about this spot.' })
  }
  const copyLink = async () => {
    const base = window.location.href.split('#')[0]
    const url = `${base}#spot=${x.toFixed(1)},${y.toFixed(1)}`
    try {
      await navigator.clipboard.writeText(url)
      toast.success('Link copied')
    } catch {
      toast.error('Couldn’t copy the link', { description: url })
    }
  }

  return (
    <ContextMenuContent className="w-72" aria-label="Plan actions" onCloseAutoFocus={onCloseAutoFocus} onFocusOutside={onFocusOutside}>
      <ContextMenuLabel className="flex items-baseline gap-1.5 px-2 pt-1.5 pb-1">
        {room ? (
          <>
            <span className="truncate text-sm font-semibold text-foreground">{roomTitle(room)}</span>
            <span className="num shrink-0 text-xs text-muted-foreground">
              · {room.area_m2 === null ? 'area not printed' : `${fmtArea(room.area_m2, lang)} m²`}
            </span>
          </>
        ) : (
          <span className="text-sm text-muted-foreground">No room here</span>
        )}
      </ContextMenuLabel>
      <ContextMenuSeparator />
      <ContextMenuGroup>
        <ContextMenuItem onSelect={askSpot}>
          <MessageCircleQuestionIcon />
          Ask about this spot
        </ContextMenuItem>
        <ContextMenuItem onSelect={() => actions().setTool('mark')}>
          <SquareDashedIcon />
          Mark an area for the question
        </ContextMenuItem>
      </ContextMenuGroup>
      <ContextMenuSeparator />
      <ContextMenuGroup>
        {room ? (
          <ContextMenuItem onSelect={() => void actions().ask(`What is the net area of room ${room.num}?`)}>
            <SquareIcon />
            <span className="truncate">Area of {roomTitle(room)}</span>
          </ContextMenuItem>
        ) : null}
        {room?.rect ? (
          <ContextMenuItem onSelect={() => actions().startWhatIf(room.room_id)}>
            <RulerIcon />
            Change sizes of {room.num}
          </ContextMenuItem>
        ) : null}
        {hasOriginal ? (
          <ContextMenuItem
            onSelect={() => {
              swipeSplit.current = Math.min(98, Math.max(2, x))
              actions().setCompare({ on: true, mode: 'swipe' })
            }}
          >
            <Columns2Icon />
            Compare with the original here
          </ContextMenuItem>
        ) : null}
      </ContextMenuGroup>
      <ContextMenuSeparator />
      <ContextMenuGroup>
        <ContextMenuItem onSelect={() => void copyLink()}>
          <LinkIcon />
          Copy link to this spot
        </ContextMenuItem>
      </ContextMenuGroup>
    </ContextMenuContent>
  )
}

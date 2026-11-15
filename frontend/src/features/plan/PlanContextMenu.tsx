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
import { fmtRoomArea } from '@/lib/format'
import { nearestRoom } from '@/lib/geometry'
import { actions, useAppStore } from '@/store'
import { useUi } from '@/i18n/uiLang'
import { markLabel, nextLabel, roomTitle, swipeSplit } from './shared'

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
  const answererMode = useAppStore((s) => s.session.answererMode)
  const { lang, tu } = useUi()
  const hasOriginal = useAppStore((s) => !!s.session.drawing.images.original)
  const [x, y] = point ?? [50, 50]
  const room = point ? nearestRoom(rooms, x, y, 6) : null

  const askSpot = () => {
    const a = actions()
    const label = nextLabel('Spot')
    a.addMark({ x: x - SPOT / 2, y: y - SPOT / 2, w: SPOT, h: SPOT }, label)
    toast(tu('ctx_spot_attached', { label: markLabel(label, tu) }), { description: tu('ctx_spot_attached_desc') })
  }
  const copyLink = async () => {
    const base = window.location.href.split('#')[0]
    const url = `${base}#spot=${x.toFixed(1)},${y.toFixed(1)}`
    try {
      await navigator.clipboard.writeText(url)
      toast.success(tu('ctx_link_copied'))
    } catch {
      toast.error(tu('ctx_link_failed'), { description: url })
    }
  }

  return (
    <ContextMenuContent className="w-72" aria-label={tu('ctx_actions')} onCloseAutoFocus={onCloseAutoFocus} onFocusOutside={onFocusOutside}>
      <ContextMenuLabel className="flex items-baseline gap-1.5 px-2 pt-1.5 pb-1">
        {room ? (
          <>
            <span className="truncate text-sm font-semibold text-foreground">{roomTitle(room)}</span>
            <span className="num shrink-0 text-xs text-muted-foreground">
              · {room.area_m2 === null ? tu('ctx_no_area') : fmtRoomArea(room.area_m2, room.area_source, lang)}
            </span>
          </>
        ) : (
          <span className="text-sm text-muted-foreground">{tu('ctx_no_room')}</span>
        )}
      </ContextMenuLabel>
      <ContextMenuSeparator />
      <ContextMenuGroup>
        <ContextMenuItem onSelect={askSpot}>
          <MessageCircleQuestionIcon />
          {tu('ctx_ask_spot')}
        </ContextMenuItem>
        <ContextMenuItem onSelect={() => actions().setTool('mark')}>
          <SquareDashedIcon />
          {tu('ctx_mark_area')}
        </ContextMenuItem>
      </ContextMenuGroup>
      <ContextMenuSeparator />
      <ContextMenuGroup>
        {room ? (
          <ContextMenuItem onSelect={() => void actions().ask(tu('ctx_room_area_q', { num: room.num }))}>
            <SquareIcon />
            <span className="truncate">{tu('ctx_area_of', { room: roomTitle(room) })}</span>
          </ContextMenuItem>
        ) : null}
        {room?.rect && answererMode !== 'agent' ? (
          <ContextMenuItem onSelect={() => actions().startWhatIf(room.room_id)}>
            <RulerIcon />
            {tu('change_sizes', { room: room.num })}
          </ContextMenuItem>
        ) : null}
        {hasOriginal ? (
          <ContextMenuItem
            onSelect={() => {
              swipeSplit.current = Math.min(100, Math.max(0, x))
              actions().setCompare({ on: true, mode: 'swipe' })
            }}
          >
            <Columns2Icon />
            {tu('ctx_compare_here')}
          </ContextMenuItem>
        ) : null}
      </ContextMenuGroup>
      <ContextMenuSeparator />
      <ContextMenuGroup>
        <ContextMenuItem onSelect={() => void copyLink()}>
          <LinkIcon />
          {tu('ctx_copy_link')}
        </ContextMenuItem>
      </ContextMenuGroup>
    </ContextMenuContent>
  )
}

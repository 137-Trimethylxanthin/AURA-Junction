import { XIcon } from 'lucide-react'
import { useStore } from 'zustand'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Kbd, KbdGroup } from '@/components/ui/kbd'
import { useUi } from '@/i18n/uiLang'
import { appStore, type AppStore } from '@/store'
import { powerRows } from './powerBindings'
import { CLOSE_DIALOG_LABEL, isMacPlatform } from './useWorkspaceShortcuts'

interface ShortcutRow {
  id: string
  label: string
  keys: readonly string[]
  /** When true, keys are alternatives (`J` or `]`) rather than a chord (`Ctrl + K`). */
  anyKey?: boolean
}

export function ShortcutHelpDialog({ store = appStore }: { store?: AppStore }) {
  const { lang, tu } = useUi()
  const open = useStore(store, (s) => s.ui.shortcutsOpen)
  const canWhatIf = useStore(
    store,
    (s) => s.session.drawing.rooms.some((r) => Boolean(r.rect)) && s.answererMode !== 'agent',
  )
  const hasOriginal = useStore(store, (s) => Boolean(s.session.drawing.images.original))

  const mac = isMacPlatform()
  const mod = mac ? '⌘' : 'Ctrl'
  const alt = mac ? '⌥' : 'Alt'

  const power = powerRows(tu, alt)

  const groups: { id: string; heading: string; rows: ShortcutRow[] }[] = [
    {
      id: 'nav',
      heading: tu('sc_group_nav'),
      rows: [
        { id: 'cmd', label: tu('sc_cmd'), keys: [mod, 'K'] },
        { id: 'ask', label: tu('sc_focus_ask'), keys: ['/'], anyKey: true },
        { id: 'floor-up', label: tu('sc_floor_up'), keys: [alt, '↑'] },
        { id: 'floor-down', label: tu('sc_floor_down'), keys: [alt, '↓'] },
        { id: 'help', label: tu('sc_help'), keys: ['?'], anyKey: true },
      ],
    },
    {
      id: 'tools',
      heading: tu('sc_group_tools'),
      rows: [
        { id: 'select', label: tu('sc_tool_select'), keys: ['1', 'V'], anyKey: true },
        { id: 'mark', label: tu('sc_tool_mark'), keys: ['2', 'M'], anyKey: true },
        ...(canWhatIf ? [{ id: 'whatif', label: tu('sc_tool_whatif'), keys: ['3', 'W'], anyKey: true }] : []),
        ...(hasOriginal
          ? [
              { id: 'compare', label: tu('sc_compare'), keys: ['C'], anyKey: true },
              { id: 'compare-mode', label: tu('sc_compare_mode'), keys: ['Shift', 'C'] },
            ]
          : []),
        { id: 'zoom-in', label: tu('sc_zoom_in'), keys: ['+', '='], anyKey: true },
        { id: 'zoom-out', label: tu('sc_zoom_out'), keys: ['-'], anyKey: true },
        { id: 'zoom-fit', label: tu('sc_zoom_fit'), keys: ['0'], anyKey: true },
      ],
    },
    {
      id: 'evidence',
      heading: tu('sc_group_evidence'),
      rows: [
        { id: 'ev-next', label: tu('sc_ev_next'), keys: ['J', ']'], anyKey: true },
        { id: 'ev-prev', label: tu('sc_ev_prev'), keys: ['K', '['], anyKey: true },
        { id: 'ev-toggle', label: tu('sc_ev_toggle'), keys: ['X', 'Del'], anyKey: true },
        { id: 'trace', label: tu('sc_trace'), keys: ['T'], anyKey: true },
        { id: 'undo', label: tu('sc_undo'), keys: [mod, 'Z'] },
      ],
    },
    {
      id: 'stage',
      heading: tu('sc_group_stage'),
      rows: power.filter((r) => r.group === 'stage'),
    },
    {
      id: 'workflow',
      heading: tu('sc_group_workflow'),
      rows: power.filter((r) => r.group === 'workflow'),
    },
  ]

  return (
    <Dialog open={open} onOpenChange={(next) => store.getState().setShortcutsOpen(next)}>
      <DialogContent
        showCloseButton={false}
        data-shortcut-help
        className="max-h-[85dvh] overflow-y-auto sm:max-w-lg duration-0 data-open:animate-none data-closed:animate-none"
      >
        <div className="flex items-start justify-between gap-3">
          <DialogHeader className="min-w-0 flex-1">
            <DialogTitle>{tu('sc_title')}</DialogTitle>
            <DialogDescription>{tu('sc_desc')}</DialogDescription>
          </DialogHeader>
          <DialogClose asChild>
            <Button
              variant="ghost"
              size="icon"
              aria-label={CLOSE_DIALOG_LABEL[lang]}
              className="-mt-1 -mr-1 shrink-0"
            >
              <XIcon />
            </Button>
          </DialogClose>
        </div>

        <div className="flex flex-col gap-4">
          {groups.map((group) => (
            <section key={group.id} aria-labelledby={`sc-heading-${group.id}`} className="flex flex-col gap-1.5">
              <h3 id={`sc-heading-${group.id}`} className="text-xs font-semibold text-muted-foreground">
                {group.heading}
              </h3>
              <dl className="divide-y divide-border rounded-lg border bg-card">
                {group.rows.map((row) => (
                  <div key={row.id} className="flex min-h-9 items-center justify-between gap-3 px-3 py-1.5">
                    <dt className="text-[13px] text-foreground">{row.label}</dt>
                    <dd className="shrink-0">
                      <KbdGroup>
                        {row.keys.map((k, idx) => (
                          <span key={`${row.id}-${k}`} className="inline-flex items-center gap-1">
                            {idx > 0 ? (
                              <span aria-hidden className="text-xs text-muted-foreground">
                                {row.anyKey ? '/' : '+'}
                              </span>
                            ) : null}
                            <Kbd>{k}</Kbd>
                          </span>
                        ))}
                      </KbdGroup>
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  )
}

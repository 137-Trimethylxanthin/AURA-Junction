import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeftIcon,
  Building2Icon,
  Columns2Icon,
  DownloadIcon,
  FileSpreadsheetIcon,
  GitBranchIcon,
  GlobeIcon,
  HistoryIcon,
  KeyboardIcon,
  LayersIcon,
  MapPinIcon,
  Maximize2Icon,
  MessageSquarePlusIcon,
  MousePointer2Icon,
  PlugIcon,
  RulerIcon,
  SparklesIcon,
  SquareDashedIcon,
  TextCursorInputIcon,
} from 'lucide-react'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'
import { useStore } from 'zustand'
import type { DrawingSummary, Lang } from '@/api/contract'
import { listDrawings } from '@/api/client'
import { apiBase, displayDrawingTitle, myProjects } from '@/api/library'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from '@/components/ui/command'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Kbd, KbdGroup } from '@/components/ui/kbd'
import { bundledDrawing } from '@/data/drawing'
import { suggestionsFor, useDraft } from '@/features/chat/draft'
import { sessionHistory, useSessionHistory } from '@/features/history'
import { roomTitle } from '@/features/plan/shared'
import { downloadText, evidenceCsv } from '@/features/plugins/csv'
import { uiLang, useUi } from '@/i18n/uiLang'
import { roomEvidenceId } from '@/lib/answer'
import { LOCALE } from '@/lib/format'
import {
  appStore,
  selectActiveProject,
  selectActiveTurn,
  selectCrossPlanScope,
  selectDrawing,
  selectIsPending,
  type AgentTurn,
  type AppStore,
  type Tool,
} from '@/store'
import { powerRows, runPowerAction } from './powerBindings'
import { isMacPlatform } from './useWorkspaceShortcuts'

const LANGUAGE_ITEMS: readonly { code: Lang; name: string }[] = [
  { code: 'en', name: 'English' },
  { code: 'fr', name: 'Français' },
  { code: 'fi', name: 'Suomi' },
  { code: 'de', name: 'Deutsch' },
]

/** Power rows that already have a dedicated palette item (which just shows the pill). */
const PALETTE_HAS_ITEM = new Set(['fit', 'all-floors', 'export', 'new-chat', 'library', 'plugins', 'history'])

export function CommandPaletteDialog({
  store = appStore,
  navigate: navigateProp,
}: {
  store?: AppStore
  navigate?: (to: string) => void
}) {
  const { lang, tu } = useUi()
  const routerNavigate = useNavigate()
  const navigate = navigateProp ?? ((to: string) => void routerNavigate(to))

  const open = useStore(store, (s) => s.ui.commandOpen)
  const drawing = useStore(store, selectDrawing)
  const activeTurn = useStore(store, selectActiveTurn)
  const pending = useStore(store, selectIsPending)
  const hasTurns = useStore(store, (s) => s.turns.length > 0)
  const canWhatIf = useStore(
    store,
    (s) => s.session.drawing.rooms.some((r) => Boolean(r.rect)) && s.answererMode !== 'agent',
  )
  const hasOriginal = useStore(store, (s) => Boolean(s.session.drawing.images.original))
  const activeProject = useStore(store, selectActiveProject)
  const crossPlanScope = useStore(store, selectCrossPlanScope)
  const allSessions = useSessionHistory((st) => st.sessions)
  const recentSessions = useMemo(
    () => [...allSessions].sort((a, b) => b.updated_at - a.updated_at).slice(0, 8),
    [allSessions],
  )

  const [remoteDrawings, setRemoteDrawings] = useState<DrawingSummary[] | null>(null)
  const skipCloseRestoreRef = useRef(false)

  useEffect(() => {
    if (!open) return
    const base = apiBase()
    if (!base || remoteDrawings !== null) return
    let cancelled = false
    void listDrawings({ baseUrl: base }, 8_000)
      .then((res) => {
        if (!cancelled) setRemoteDrawings(res.drawings)
      })
      .catch(() => {
        if (!cancelled) setRemoteDrawings([])
      })
    return () => {
      cancelled = true
    }
  }, [open, remoteDrawings])

  const drawingsList = useMemo(() => {
    const map = new Map<string, DrawingSummary>()
    map.set(bundledDrawing.id, {
      id: bundledDrawing.id,
      title: bundledDrawing.title,
      kind: bundledDrawing.kind ?? 'cad_dxf',
      status: 'ready',
      thumbnail: bundledDrawing.images.rebuilt,
      credit: bundledDrawing.credit,
    })
    map.set(drawing.id, {
      id: drawing.id,
      title: drawing.title,
      kind: drawing.kind ?? 'cad_dxf',
      status: 'ready',
      thumbnail: drawing.images.rebuilt,
      credit: drawing.credit,
      project_id: drawing.project_id,
      floor_label: drawing.floor_label,
      floor_order: drawing.floor_order,
    })
    for (const d of remoteDrawings ?? []) {
      map.set(d.id, d)
    }
    return [...map.values()]
  }, [drawing, remoteDrawings])

  const resolvedProjects = useMemo(
    () => (open ? myProjects.resolveAll(drawingsList) : []),
    [open, drawingsList],
  )

  const orderedFloors = useMemo(() => {
    if (!activeProject || activeProject.floors.length < 2) return []
    return [...activeProject.floors].sort(
      (a, b) => a.order - b.order || a.drawing_id.localeCompare(b.drawing_id),
    )
  }, [activeProject])

  const suggestions = useMemo(() => suggestionsFor(drawing, activeProject), [drawing, activeProject])

  const hasExportableEvidence = Boolean(
    activeTurn?.response &&
    (activeTurn.response.evidence.boxes.length > 0 ||
      activeTurn.response.evidence.rooms.length > 0 ||
      (activeTurn.response.evidence.rows?.length ?? 0) > 0),
  )

  const areaFmt = useMemo(
    () =>
      new Intl.NumberFormat(LOCALE[lang], {
        minimumFractionDigits: 1,
        maximumFractionDigits: 2,
      }),
    [lang],
  )

  const mac = isMacPlatform()
  const alt = mac ? '⌥' : 'Alt'
  const currentFloorIdx = useMemo(
    () => orderedFloors.findIndex((f) => f.drawing_id === drawing.id),
    [orderedFloors, drawing.id],
  )

  const runCommand = (fn: () => void, opts: { managesOwnFocus?: boolean } = {}) => {
    if (opts.managesOwnFocus) skipCloseRestoreRef.current = true
    store.getState().setCommandOpen(false)
    fn()
  }

  const pickTool = (tool: Tool) => {
    runCommand(() => {
      const s = store.getState()
      if (s.ui.tool === 'whatif' && tool !== 'whatif') s.cancelWhatIf()
      s.setTool(tool)
    })
  }

  return (
    <Dialog open={open} onOpenChange={(next) => store.getState().setCommandOpen(next)}>
      <DialogContent
        showCloseButton={false}
        data-command-palette
        onCloseAutoFocus={(e) => {
          if (skipCloseRestoreRef.current) {
            e.preventDefault()
            skipCloseRestoreRef.current = false
          }
        }}
        className="top-[18%] translate-y-0 overflow-hidden rounded-xl! p-0 duration-0 sm:max-w-lg data-open:animate-none data-closed:animate-none"
      >
        <DialogHeader className="sr-only">
          <DialogTitle>{tu('cmd_title')}</DialogTitle>
          <DialogDescription>{tu('cmd_desc')}</DialogDescription>
        </DialogHeader>
        <Command className="[&_[data-slot=input-group]]:h-10! [&_[data-slot=input-group]]:min-h-10 [&_[data-slot=input-group]]:border-input [&_[data-slot=input-group]]:bg-background">
          <CommandInput placeholder={tu('cmd_placeholder')} aria-label={tu('cmd_title')} />
          <CommandList className="max-h-80">
            <CommandEmpty>{tu('cmd_empty')}</CommandEmpty>

            {orderedFloors.length >= 2 && activeProject ? (
              <>
                <CommandGroup heading={`${tu('cmd_group_floors')} · ${activeProject.title}`}>
                  {orderedFloors.map((floor, idx) => {
                    const isCurrent = floor.drawing_id === drawing.id
                    const stepArrow =
                      currentFloorIdx >= 0 && idx === currentFloorIdx + 1
                        ? '↑'
                        : currentFloorIdx >= 0 && idx === currentFloorIdx - 1
                          ? '↓'
                          : null
                    return (
                      <CommandItem
                        key={`floor-${floor.drawing_id}`}
                        value={`floor ${floor.label} ${activeProject.title} ${floor.drawing_id}`}
                        data-checked={isCurrent ? 'true' : undefined}
                        className="min-h-10"
                        onSelect={() =>
                          runCommand(() => {
                            if (!isCurrent) navigate(`/d/${floor.drawing_id}`)
                          })
                        }
                      >
                        <LayersIcon />
                        <span className="truncate">{tu('group_jump_floor', { label: floor.label })}</span>
                        {stepArrow ? (
                          <CommandShortcut>
                            <KbdGroup>
                              <Kbd>{alt}</Kbd>
                              <Kbd>{stepArrow}</Kbd>
                            </KbdGroup>
                          </CommandShortcut>
                        ) : idx < 9 ? (
                          <CommandShortcut>
                            <KbdGroup>
                              <Kbd>{alt}</Kbd>
                              <Kbd>{idx + 1}</Kbd>
                            </KbdGroup>
                          </CommandShortcut>
                        ) : null}
                      </CommandItem>
                    )
                  })}
                  <CommandItem
                    value={`match all floors ${tu('match_all_floors')} ${activeProject.title}`}
                    data-checked={crossPlanScope ? 'true' : undefined}
                    className="min-h-10"
                    onSelect={() =>
                      runCommand(() => {
                        store.getState().setCrossPlanScope(!crossPlanScope)
                      })
                    }
                  >
                    <Building2Icon />
                    <span className="truncate">{tu('match_all_floors')}</span>

                    <CommandShortcut>
                      <KbdGroup>
                        <Kbd>{alt}</Kbd>
                        <Kbd>A</Kbd>
                      </KbdGroup>
                    </CommandShortcut>
                  </CommandItem>
                </CommandGroup>
                <CommandSeparator />
              </>
            ) : null}

            <CommandGroup heading={tu('cmd_group_queries')}>
              {suggestions.map((s) => (
                <CommandItem
                  key={s.text}
                  value={`ask query ${s.lang} ${s.short} ${s.text}`}
                  disabled={pending}
                  className="min-h-10"
                  onSelect={() =>
                    runCommand(() => {
                      void store.getState().ask(s.text)
                    })
                  }
                >
                  <SparklesIcon />
                  <span className="num text-xs font-semibold text-muted-foreground">{s.lang}</span>
                  <span className="truncate">{s.text}</span>
                </CommandItem>
              ))}
            </CommandGroup>

            <CommandSeparator />

            <CommandGroup heading={tu('cmd_group_actions')}>
              <CommandItem
                value={`focus question composer ask ${tu('sc_focus_ask')}`}
                className="min-h-10"
                onSelect={() =>
                  runCommand(
                    () => {
                      requestAnimationFrame(() => useDraft.getState().focus())
                    },
                    { managesOwnFocus: true },
                  )
                }
              >
                <TextCursorInputIcon />
                <span>{tu('sc_focus_ask')}</span>
                <CommandShortcut>
                  <Kbd>/</Kbd>
                </CommandShortcut>
              </CommandItem>

              <CommandItem
                value={`tool select ${tu('sc_tool_select')}`}
                className="min-h-10"
                onSelect={() => pickTool('select')}
              >
                <MousePointer2Icon />
                <span>{tu('sc_tool_select')}</span>
                <CommandShortcut>
                  <Kbd>1</Kbd>
                </CommandShortcut>
              </CommandItem>

              <CommandItem
                value={`tool mark area ${tu('sc_tool_mark')}`}
                className="min-h-10"
                onSelect={() => pickTool('mark')}
              >
                <SquareDashedIcon />
                <span>{tu('sc_tool_mark')}</span>
                <CommandShortcut>
                  <Kbd>2</Kbd>
                </CommandShortcut>
              </CommandItem>

              {canWhatIf ? (
                <CommandItem
                  value={`tool what-if sizes ${tu('sc_tool_whatif')}`}
                  className="min-h-10"
                  onSelect={() => pickTool('whatif')}
                >
                  <RulerIcon />
                  <span>{tu('sc_tool_whatif')}</span>
                  <CommandShortcut>
                    <Kbd>3</Kbd>
                  </CommandShortcut>
                </CommandItem>
              ) : null}

              {hasOriginal ? (
                <CommandItem
                  value={`compare overlay original ${tu('sc_compare')}`}
                  className="min-h-10"
                  onSelect={() =>
                    runCommand(() => {
                      const s = store.getState()
                      s.setCompare({ on: !s.ui.compare.on })
                    })
                  }
                >
                  <Columns2Icon />
                  <span>{tu('sc_compare')}</span>
                  <CommandShortcut>
                    <Kbd>C</Kbd>
                  </CommandShortcut>
                </CommandItem>
              ) : null}

              <CommandItem
                value={`zoom fit reset 100 ${tu('sc_zoom_fit')}`}
                className="min-h-10"
                onSelect={() => runCommand(() => store.getState().resetZoom())}
              >
                <Maximize2Icon />
                <span>{tu('sc_zoom_fit')}</span>
                <CommandShortcut>
                  <KbdGroup>
                    <Kbd>0</Kbd>
                    <Kbd>F</Kbd>
                  </KbdGroup>
                </CommandShortcut>
              </CommandItem>

              <CommandItem
                value={`reasoning trace tree ${tu('sc_trace')}`}
                className="min-h-10"
                onSelect={() =>
                  runCommand(
                    () => {
                      const s = store.getState()
                      const nextOpen = !s.ui.traceOpen
                      if (nextOpen && !s.ui.activeTurnId) {
                        const latestAgent = [...s.turns]
                          .reverse()
                          .find((turn): turn is AgentTurn => turn.role === 'agent')
                        if (latestAgent) s.setActive(latestAgent.id)
                      }
                      s.setTraceOpen(nextOpen)
                      if (!nextOpen) {
                        requestAnimationFrame(() => useDraft.getState().focus())
                      }
                    },
                    { managesOwnFocus: true },
                  )
                }
              >
                <GitBranchIcon />
                <span>{tu('sc_trace')}</span>
                <CommandShortcut>
                  <Kbd>T</Kbd>
                </CommandShortcut>
              </CommandItem>

              {hasExportableEvidence && activeTurn?.response ? (
                <CommandItem
                  value={`export download csv evidence ${tu('cmd_export_csv')}`}
                  className="min-h-10"
                  onSelect={() =>
                    runCommand(() => {
                      if (!activeTurn.response) return
                      const csv = evidenceCsv(activeTurn.response, activeTurn.toggles, drawing)
                      downloadText(`${drawing.id}-evidence.csv`, csv)
                    })
                  }
                >
                  <DownloadIcon />
                  <span>{tu('cmd_export_csv')}</span>

                  <CommandShortcut>
                    <KbdGroup>
                      <Kbd>E</Kbd>
                    </KbdGroup>
                  </CommandShortcut>
                </CommandItem>
              ) : null}

              {hasTurns ? (
                <CommandItem
                  value={`new conversation clear chat ${tu('new_chat')}`}
                  className="min-h-10"
                  onSelect={() =>
                    runCommand(() => {
                      const restore = store.getState().newConversation()
                      toast(tu('hist_saved'), {
                        id: 'new-chat-undo',
                        toasterId: 'chat',
                        duration: 6000,
                        action: { label: tu('undo'), onClick: () => restore() },
                      })
                    })
                  }
                >
                  <MessageSquarePlusIcon />
                  <span>{tu('new_chat')}</span>

                  <CommandShortcut>
                    <KbdGroup>
                      <Kbd>{alt}</Kbd>
                      <Kbd>N</Kbd>
                    </KbdGroup>
                  </CommandShortcut>
                </CommandItem>
              ) : null}

              <CommandItem
                value={`history sessions past conversations ${tu('hist_open')}`}
                className="min-h-10"
                onSelect={() => runCommand(() => sessionHistory.setOpen(true))}
              >
                <HistoryIcon />
                <span>{tu('hist_open')}</span>
                <CommandShortcut>
                  <KbdGroup>
                    <Kbd>{alt}</Kbd>
                    <Kbd>H</Kbd>
                  </KbdGroup>
                </CommandShortcut>
              </CommandItem>

              <CommandItem
                value={`plugins mcp extensions ${tu('plugins')}`}
                className="min-h-10"
                onSelect={() =>
                  runCommand(() => store.getState().setPluginsOpen(true), {
                    managesOwnFocus: true,
                  })
                }
              >
                <PlugIcon />
                <span>{tu('plugins')}</span>

                <CommandShortcut>
                  <KbdGroup>
                    <Kbd>{alt}</Kbd>
                    <Kbd>P</Kbd>
                  </KbdGroup>
                </CommandShortcut>
              </CommandItem>

              <CommandItem
                value={`keyboard shortcuts help ${tu('sc_help')}`}
                className="min-h-10"
                onSelect={() =>
                  runCommand(() => store.getState().setShortcutsOpen(true), {
                    managesOwnFocus: true,
                  })
                }
              >
                <KeyboardIcon />
                <span>{tu('sc_help')}</span>
                <CommandShortcut>
                  <KbdGroup>
                    <Kbd>?</Kbd>
                  </KbdGroup>
                </CommandShortcut>
              </CommandItem>

              {powerRows(tu, alt)
                .filter((r) => r.action && !PALETTE_HAS_ITEM.has(r.id))
                .map((row) => (
                  <CommandItem
                    key={`power-${row.id}`}
                    value={`power shortcut ${row.id} ${row.label}`}
                    className="min-h-10"
                    onSelect={() =>
                      runCommand(() => {
                        runPowerAction(row.action!, { store, navigate })
                      })
                    }
                  >
                    <KeyboardIcon />
                    <span className="truncate">{row.label}</span>
                    <CommandShortcut>
                      <KbdGroup>
                        {row.keys.map((k) => (
                          <Kbd key={k}>{k}</Kbd>
                        ))}
                      </KbdGroup>
                    </CommandShortcut>
                  </CommandItem>
                ))}

              <CommandItem
                value={`library all drawings home ${tu('all_drawings')}`}
                className="min-h-10"
                onSelect={() => runCommand(() => navigate('/app'))}
              >
                <ArrowLeftIcon />
                <span>{tu('all_drawings')}</span>

                <CommandShortcut>
                  <KbdGroup>
                    <Kbd>{alt}</Kbd>
                    <Kbd>L</Kbd>
                  </KbdGroup>
                </CommandShortcut>
              </CommandItem>
            </CommandGroup>

            {drawing.rooms.length > 0 ? (
              <>
                <CommandSeparator />
                <CommandGroup heading={tu('cmd_group_rooms')}>
                  {drawing.rooms.map((room) => {
                    const label = roomTitle(room)
                    const [rx, ry] = room.label_at
                    return (
                      <CommandItem
                        key={room.room_id}
                        value={`room ${label} ${room.unit ?? ''} ${room.room_id}`}
                        className="min-h-10"
                        onSelect={() =>
                          runCommand(() => {
                            const evId = roomEvidenceId(room.room_id)
                            if (
                              activeTurn?.response?.evidence.rooms.some((er) => er.room_id === room.room_id)
                            ) {
                              store.getState().select(activeTurn.id, evId)
                            }
                            const spotX = Math.round(rx * 10) / 10
                            const spotY = Math.round(ry * 10) / 10
                            const spotHash = `#spot=${spotX},${spotY}`
                            navigate(`/d/${drawing.id}${spotHash}`)
                            if (typeof window !== 'undefined') {
                              // React Router uses `history.pushState()`, which does not emit `hashchange`.
                              // Sync hash & dispatch `hashchange` so `PlanStage` pans and zooms (>=190%) every time.
                              const oldURL = window.location.href
                              if (window.location.hash !== spotHash) {
                                window.history.replaceState(
                                  window.history.state,
                                  '',
                                  `${window.location.pathname}${window.location.search}${spotHash}`,
                                )
                              }
                              const ev =
                                typeof HashChangeEvent === 'function'
                                  ? new HashChangeEvent('hashchange', {
                                      oldURL,
                                      newURL: window.location.href,
                                    })
                                  : new Event('hashchange')
                              window.dispatchEvent(ev)
                            }
                          })
                        }
                      >
                        <MapPinIcon />
                        <span className="truncate">{label}</span>
                        {room.area_m2 !== null ? (
                          <span className="num ml-auto text-xs text-muted-foreground">
                            {areaFmt.format(room.area_m2)} m²
                          </span>
                        ) : null}
                      </CommandItem>
                    )
                  })}
                </CommandGroup>
              </>
            ) : null}

            {resolvedProjects.length > 0 ? (
              <>
                <CommandSeparator />
                <CommandGroup heading={tu('cmd_group_projects')}>
                  {resolvedProjects.map((proj) => {
                    const firstFloor = [...proj.floors].sort((a, b) => a.order - b.order)[0]
                    if (!firstFloor) return null
                    return (
                      <CommandItem
                        key={`project-${proj.id}`}
                        value={`project house bundle ${proj.title} ${proj.floors.map((f) => f.label).join(' ')}`}
                        data-checked={activeProject?.id === proj.id ? 'true' : undefined}
                        className="min-h-10"
                        onSelect={() =>
                          runCommand(() => {
                            store.getState().setActiveProject(proj)
                            navigate(`/d/${firstFloor.drawing_id}`)
                          })
                        }
                      >
                        <Building2Icon />
                        <span className="truncate">{proj.title}</span>
                        <span className="num ml-auto text-xs text-muted-foreground">
                          {tu('proj_floors_n', { n: proj.floors.length })}
                        </span>
                      </CommandItem>
                    )
                  })}
                </CommandGroup>
              </>
            ) : null}

            {recentSessions.length > 0 ? (
              <>
                <CommandSeparator />
                <CommandGroup heading={tu('hist_recent')}>
                  {recentSessions.map((s) => (
                    <CommandItem
                      key={s.id}
                      value={`session ${s.title} ${s.drawing_title ?? ''} ${s.id}`}
                      className="min-h-10"
                      onSelect={() => runCommand(() => void sessionHistory.restore(s.id, navigate))}
                    >
                      <HistoryIcon />
                      <span className="truncate">{s.title}</span>
                      {s.floor_label ?? s.drawing_title ? (
                        <span className="ml-auto max-w-[40%] truncate text-xs text-muted-foreground">
                          {s.floor_label ?? s.drawing_title}
                        </span>
                      ) : null}
                    </CommandItem>
                  ))}
                </CommandGroup>
              </>
            ) : null}

            <CommandSeparator />

            <CommandGroup heading={tu('cmd_group_drawings')}>
              {drawingsList.map((d) => {
                const title = displayDrawingTitle(d)
                const isCurrent = d.id === drawing.id
                return (
                  <CommandItem
                    key={d.id}
                    value={`drawing plan ${title} ${d.id}`}
                    data-checked={isCurrent ? 'true' : undefined}
                    className="min-h-10"
                    onSelect={() =>
                      runCommand(() => {
                        if (!isCurrent) navigate(`/d/${d.id}`)
                      })
                    }
                  >
                    <FileSpreadsheetIcon />
                    <span className="truncate">{title}</span>
                  </CommandItem>
                )
              })}
            </CommandGroup>

            <CommandSeparator />

            <CommandGroup heading={tu('cmd_group_lang')}>
              {LANGUAGE_ITEMS.map((item) => (
                <CommandItem
                  key={item.code}
                  value={`language locale ${item.name} ${item.code}`}
                  data-checked={lang === item.code ? 'true' : undefined}
                  className="min-h-10"
                  onSelect={() => runCommand(() => uiLang.set(item.code))}
                >
                  <GlobeIcon />
                  <span>{item.name}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  )
}

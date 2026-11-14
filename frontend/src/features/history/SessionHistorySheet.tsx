import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react"
import { useNavigate } from "react-router"
import {
  DownloadIcon,
  FileTextIcon,
  HistoryIcon,
  MoreHorizontalIcon,
  PencilIcon,
  PinIcon,
  PinOffIcon,
  SearchIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group"
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { downloadText } from "@/features/plugins/csv"
import type { UiKey } from "@/i18n/strings"
import { useUi } from "@/i18n/uiLang"
import { cn } from "@/lib/utils"
import { useAppStore } from "@/store"
import { sessionHistory, useSessionHistory } from "."
import {
  filterSessions,
  groupSessions,
  questionCount,
  sessionCsv,
  sessionFileName,
  sessionMarkdown,
  type SessionFilter,
  type SessionGroupKey,
  type StoredSession,
} from "./sessionStore"

const GROUP_LABEL: Record<SessionGroupKey, UiKey> = {
  pinned: "hist_pinned",
  today: "hist_today",
  yesterday: "hist_yesterday",
  earlier: "hist_earlier",
}

/** Undo over a modal sheet: Radix sets `pointer-events: none` on <body>, which the toaster inherits. */
const TOAST_CLICKABLE = "[data-sonner-toaster]{pointer-events:auto}"
const insideToaster = (e: Event) =>
  (e.target as Element | null)?.closest?.("[data-sonner-toaster]") != null

function SessionRow({
  session,
  current,
  restoring,
  timeLabel,
  onOpen,
}: {
  session: StoredSession
  current: boolean
  restoring: boolean
  timeLabel: string
  onOpen: (s: StoredSession) => void
}) {
  const { tu, tn } = useUi()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(session.title)
  const inputRef = useRef<HTMLInputElement>(null)
  const rowRef = useRef<HTMLButtonElement>(null)
  /** Rename picked from the menu: keep focus in the field instead of returning it to the menu trigger. */
  const renaming = useRef(false)

  useEffect(() => {
    if (!editing) return
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [editing])

  const startRename = () => {
    setDraft(session.title)
    setEditing(true)
  }
  const finishRename = (save: boolean) => {
    if (save && draft.trim() !== session.title)
      sessionHistory.rename(session.id, draft)
    setEditing(false)
    requestAnimationFrame(() => rowRef.current?.focus())
  }
  const remove = () => {
    const undo = sessionHistory.remove(session.id)
    toast(tu("hist_deleted"), {
      description: session.title,
      duration: 6000,
      action: { label: tu("undo"), onClick: undo },
    })
  }
  const onRowKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === "F2") {
      e.preventDefault()
      startRename()
    } else if (e.key === "Delete") {
      e.preventDefault()
      remove()
    }
  }

  const where = [
    session.project_title,
    session.floor_label ?? session.drawing_title,
  ]
    .filter(Boolean)
    .join(" · ")

  return (
    <li
      className={cn(
        "group/row relative flex items-stretch gap-1 rounded-xl border bg-card pr-1 transition-[background-color,border-color] duration-150 ease-out",
        current
          ? "border-info/40 bg-info-surface/40"
          : "hover:border-foreground/15 hover:bg-muted/40"
      )}
    >
      {editing ? (
        <form
          className="flex min-h-14 flex-1 items-center gap-2 py-2 pl-3"
          onSubmit={(e) => {
            e.preventDefault()
            finishRename(true)
          }}
        >
          <Input
            ref={inputRef}
            data-hist-rename
            value={draft}
            maxLength={120}
            aria-label={tu("hist_rename_label")}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => finishRename(true)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault()
                e.stopPropagation()
                finishRename(false)
              }
            }}
          />
        </form>
      ) : (
        <button
          ref={rowRef}
          type="button"
          onClick={() => onOpen(session)}
          onKeyDown={onRowKey}
          aria-current={current ? "true" : undefined}
          aria-busy={restoring || undefined}
          className="flex min-h-14 min-w-0 flex-1 flex-col items-start gap-1 rounded-xl py-2.5 pl-3 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50 aria-busy:opacity-60 motion-safe:transition-transform motion-safe:duration-150 motion-safe:ease-[cubic-bezier(0.23,1,0.32,1)] motion-safe:active:scale-[0.99]"
        >
          <span className="line-clamp-2 text-sm font-medium text-foreground">
            {session.title}
          </span>
          <span className="flex w-full min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
            {current ? (
              <Badge variant="secondary" className="shrink-0">
                {tu("hist_current")}
              </Badge>
            ) : null}
            {where ? <span className="min-w-0 truncate">{where}</span> : null}
            {where ? <span aria-hidden>·</span> : null}
            <span className="num shrink-0">
              {tn("hist_questions", questionCount(session))}
            </span>
            <span aria-hidden>·</span>
            <time
              className="num shrink-0"
              dateTime={new Date(session.updated_at).toISOString()}
            >
              {timeLabel}
            </time>
          </span>
        </button>
      )}
      <div className="flex shrink-0 items-center">
        <Button
          variant="ghost"
          size="icon"
          aria-label={session.pinned ? tu("hist_unpin") : tu("hist_pin")}
          aria-pressed={session.pinned}
          title={session.pinned ? tu("hist_unpin") : tu("hist_pin")}
          onClick={() => sessionHistory.togglePin(session.id)}
          className={cn(
            session.pinned
              ? "text-info"
              : "text-muted-foreground transition-opacity duration-150 ease-out sm:opacity-0 sm:group-focus-within/row:opacity-100 sm:group-hover/row:opacity-100"
          )}
        >
          {session.pinned ? <PinIcon className="fill-current" /> : <PinIcon />}
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="text-muted-foreground"
              aria-label={tu("hist_more", { title: session.title })}
            >
              <MoreHorizontalIcon />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="end"
            className="min-w-52"
            onCloseAutoFocus={(e) => {
              if (!renaming.current) return
              renaming.current = false
              e.preventDefault()
            }}
          >
            <DropdownMenuGroup>
              <DropdownMenuItem
                className="min-h-10"
                onSelect={() => {
                  renaming.current = true
                  startRename()
                }}
              >
                <PencilIcon />
                {tu("hist_rename")}
                <span className="ml-auto text-xs text-muted-foreground">
                  F2
                </span>
              </DropdownMenuItem>
              <DropdownMenuItem
                className="min-h-10"
                onSelect={() => sessionHistory.togglePin(session.id)}
              >
                {session.pinned ? <PinOffIcon /> : <PinIcon />}
                {session.pinned ? tu("hist_unpin") : tu("hist_pin")}
              </DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuItem
                className="min-h-10"
                onSelect={() =>
                  downloadText(
                    sessionFileName(session, "md"),
                    sessionMarkdown(session),
                    "text/markdown;charset=utf-8"
                  )
                }
              >
                <FileTextIcon />
                {tu("hist_export_md")}
              </DropdownMenuItem>
              <DropdownMenuItem
                className="min-h-10"
                onSelect={() =>
                  downloadText(
                    sessionFileName(session, "csv"),
                    sessionCsv(session)
                  )
                }
              >
                <DownloadIcon />
                {tu("hist_export_csv")}
              </DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuItem
                className="min-h-10"
                variant="destructive"
                onSelect={remove}
              >
                <Trash2Icon />
                {tu("hist_delete")}
                <span className="ml-auto text-xs opacity-70">Del</span>
              </DropdownMenuItem>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </li>
  )
}

/** Session history drawer: grouped, filterable list of past conversations; opening one restores it on the plan. */
export function SessionHistorySheet() {
  const { tu, lang } = useUi()
  const navigate = useNavigate()
  const { open, openedAt, sessions, currentId, restoring, setOpen } =
    useSessionHistory()
  const drawingId = useAppStore((s) => s.session.drawing.id)
  const projectId = useAppStore((s) =>
    s.activeProject?.floors.some((f) => f.drawing_id === s.session.drawing.id)
      ? s.activeProject.id
      : null
  )
  const [filter, setFilter] = useState<SessionFilter>("here")
  const [query, setQuery] = useState("")

  // Save the thread on screen when the sheet opens, so it is listed (and marked "Open now").
  useEffect(() => {
    if (open) sessionHistory.saveCurrent()
  }, [open])

  const groups = useMemo(
    () =>
      groupSessions(
        filterSessions(sessions, filter, { drawingId, projectId }, query),
        openedAt
      ),
    [sessions, filter, drawingId, projectId, query, openedAt]
  )
  const fmt = useMemo(() => {
    const time = new Intl.DateTimeFormat(lang, {
      hour: "2-digit",
      minute: "2-digit",
    })
    const date = new Intl.DateTimeFormat(lang, {
      day: "numeric",
      month: "short",
    })
    return { time, date }
  }, [lang])

  const openSession = (s: StoredSession) => {
    setOpen(false)
    if (s.id === currentId) return
    const moving = s.drawing_id !== drawingId
    const tid = moving
      ? toast.loading(tu("hist_restoring"), { description: s.title })
      : undefined
    void sessionHistory
      .restore(s.id, (path) => void navigate(path))
      .then((ok) => {
        if (ok && tid !== undefined)
          toast.success(tu("hist_restored"), { id: tid, description: s.title })
        else if (!ok)
          toast.error(tu("hist_restore_failed"), {
            id: tid,
            description: s.title,
          })
      })
  }

  const total = sessions.length
  const shown = groups.reduce((n, g) => n + g.sessions.length, 0)

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetContent
        side="right"
        showCloseButton={false}
        className="w-full gap-0 p-0 data-[side=right]:sm:max-w-md"
        onPointerDownOutside={(e) => insideToaster(e) && e.preventDefault()}
        onInteractOutside={(e) => insideToaster(e) && e.preventDefault()}
        // Escape in the rename field cancels the rename, not the whole sheet.
        onEscapeKeyDown={(e) => {
          if ((e.target as Element | null)?.closest?.("[data-hist-rename]"))
            e.preventDefault()
        }}
      >
        {open ? <style>{TOAST_CLICKABLE}</style> : null}
        <SheetHeader className="flex-row items-start gap-3 border-b">
          <div className="flex flex-1 flex-col gap-0.5">
            <SheetTitle className="flex items-center gap-2">
              <HistoryIcon className="size-4 text-muted-foreground" aria-hidden />
              {tu("hist_title")}
            </SheetTitle>
            <SheetDescription>{tu("hist_desc")}</SheetDescription>
          </div>
          <SheetClose asChild>
            <Button variant="ghost" size="icon" aria-label={tu("hist_close")}>
              <XIcon />
            </Button>
          </SheetClose>
        </SheetHeader>

        <div className="flex flex-col gap-2.5 border-b p-4">
          <InputGroup className="h-10">
            <InputGroupAddon>
              <SearchIcon />
            </InputGroupAddon>
            <InputGroupInput
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={tu("hist_search")}
              aria-label={tu("hist_search")}
            />
          </InputGroup>
          <ToggleGroup
            type="single"
            variant="outline"
            spacing={0}
            value={filter}
            onValueChange={(v) => v && setFilter(v as SessionFilter)}
            aria-label={tu("hist_filter_label")}
            className="w-full"
          >
            <ToggleGroupItem value="here" className="min-h-10 flex-1 px-3">
              {projectId
                ? tu("hist_filter_project")
                : tu("hist_filter_drawing")}
            </ToggleGroupItem>
            <ToggleGroupItem value="all" className="min-h-10 flex-1 px-3">
              {tu("hist_filter_all")}
            </ToggleGroupItem>
          </ToggleGroup>
        </div>

        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 pt-3 pb-6">
          {shown === 0 ? (
            <Empty className="my-auto border-0">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <HistoryIcon />
                </EmptyMedia>
                <EmptyTitle>
                  {query.trim() ? tu("hist_search") : tu("hist_empty_title")}
                </EmptyTitle>
                <EmptyDescription>
                  {query.trim()
                    ? tu("hist_no_match", { q: query.trim() })
                    : total > 0
                      ? tu("hist_empty_filter")
                      : tu("hist_empty_desc")}
                </EmptyDescription>
              </EmptyHeader>
              {total > 0 && filter === "here" && !query.trim() ? (
                <Button variant="outline" onClick={() => setFilter("all")}>
                  {tu("hist_filter_all")}
                </Button>
              ) : null}
            </Empty>
          ) : (
            groups.map((g) => (
              <section
                key={g.key}
                aria-labelledby={`hist-group-${g.key}`}
                className="flex flex-col gap-1.5 pb-4"
              >
                <h3
                  id={`hist-group-${g.key}`}
                  className="sticky top-0 z-10 -mx-4 bg-background/95 px-4 py-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase backdrop-blur-sm"
                >
                  {tu(GROUP_LABEL[g.key])}
                </h3>
                <ul className="flex flex-col gap-1.5">
                  {g.sessions.map((s) => {
                    const sameDay = g.key === "today" || g.key === "yesterday"
                    const label = sameDay
                      ? fmt.time.format(s.updated_at)
                      : fmt.date.format(s.updated_at)
                    return (
                      <SessionRow
                        key={s.id}
                        session={s}
                        current={s.id === currentId}
                        restoring={s.id === restoring}
                        timeLabel={label}
                        onOpen={openSession}
                      />
                    )
                  })}
                </ul>
              </section>
            ))
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

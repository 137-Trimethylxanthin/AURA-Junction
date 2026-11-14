import { useState, type ReactNode } from "react"
import { ChevronsDownIcon, ChevronsUpIcon } from "lucide-react"
import { Link } from "react-router"

import { Button } from "@/components/ui/button"

import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { useMediaQuery } from "@/hooks/use-media-query"
import { cn } from "@/lib/utils"

import { Wordmark } from "./Wordmark"

export type AppShellSlots = {
  /** Drawing title, shown next to the wordmark. */
  title?: ReactNode
  /** Right side of the header: Plugins button, session cost button. */
  headerActions?: ReactNode
  /** Plan toolbar, above the stage. */
  toolbar?: ReactNode
  /** The drawing stage. Fills the remaining plan area. */
  stage?: ReactNode
  /** Credit line under the stage. */
  credit?: ReactNode
  /** Scrolling chat region of the sidebar. */
  chat?: ReactNode
  /** Composer, pinned at the bottom of the sidebar. */
  composer?: ReactNode
  /** Overlay inside the sidebar (reasoning tree). Rendered over the chat region. */
  sidebarOverlay?: ReactNode
}

/** Desktop split from this width up; below it the chat is a sticky bottom panel. */
export const SPLIT_QUERY = "(min-width: 900px)"

function Header({ title, headerActions }: Pick<AppShellSlots, "title" | "headerActions">) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-background px-4 pt-[env(safe-area-inset-top)]">
      <Link to="/app" aria-label="AURA: all drawings" className="-mx-1 flex h-10 shrink-0 items-center rounded-md px-1">
        <Wordmark compact />
      </Link>
      <div className="h-5 w-px shrink-0 bg-border" aria-hidden />
      <div className="min-w-0 flex-1 truncate text-sm text-muted-foreground" data-slot="title">
        {title}
      </div>
      <div className="flex shrink-0 items-center gap-2" data-slot="header-actions">
        {headerActions}
      </div>
    </header>
  )
}

function PlanRegion({ toolbar, stage, credit }: Pick<AppShellSlots, "toolbar" | "stage" | "credit">) {
  return (
    <section aria-label="Drawing" className="flex h-full min-h-0 min-w-0 flex-1 flex-col bg-muted/40">
      <div className="flex shrink-0 items-center gap-2 border-b bg-background px-3 py-2" data-slot="toolbar">
        {toolbar}
      </div>
      <div className="relative min-h-0 flex-1" data-slot="stage">
        {stage}
      </div>
      <div className="shrink-0 border-t bg-background px-3 py-1.5 text-xs text-muted-foreground" data-slot="credit">
        {credit}
      </div>
    </section>
  )
}

function Sidebar({
  chat,
  composer,
  sidebarOverlay,
  className,
  expand,
}: Pick<AppShellSlots, "chat" | "composer" | "sidebarOverlay"> & {
  className?: string
  expand?: { expanded: boolean; onToggle: () => void }
}) {
  return (
    <aside aria-label="Drawing agent" className={cn("flex h-full min-h-0 min-w-0 flex-col bg-background", className)}>
      <div className="flex h-11 shrink-0 items-center justify-between border-b pr-1 pl-4">
        <h2 className="text-sm font-semibold">Drawing agent</h2>
        {expand ? (
          <Button
            variant="ghost"
            size="icon"
            aria-expanded={expand.expanded}
            aria-label={expand.expanded ? "Collapse chat" : "Expand chat"}
            onClick={expand.onToggle}
          >
            {expand.expanded ? <ChevronsDownIcon /> : <ChevronsUpIcon />}
          </Button>
        ) : null}
      </div>
      <div className="relative min-h-0 flex-1">
        <div
          className="absolute inset-0 overflow-y-auto overscroll-contain focus-visible:outline-offset-[-2px]"
          data-slot="chat"
          role="region"
          aria-label="Conversation"
          tabIndex={0}
        >
          {chat}
        </div>
        {sidebarOverlay ? (
          <div
            className="pointer-events-none absolute inset-0 z-10 flex min-h-0 flex-col empty:hidden *:pointer-events-auto *:bg-background"
            data-slot="sidebar-overlay"
          >
            {sidebarOverlay}
          </div>
        ) : null}
      </div>
      <div className="shrink-0 border-t bg-background p-3 pb-safe" data-slot="composer">
        {composer}
      </div>
    </aside>
  )
}

export function AppShell(props: AppShellSlots) {
  const wide = useMediaQuery(SPLIT_QUERY, true)
  const [chatExpanded, setChatExpanded] = useState(false)
  const { title, headerActions, toolbar, stage, credit, chat, composer, sidebarOverlay } = props

  return (
    <div className="flex h-dvh w-full flex-col overflow-hidden bg-background">
      <Header title={title} headerActions={headerActions} />
      {wide ? (
        <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
          <ResizablePanel minSize={360}>
            <PlanRegion toolbar={toolbar} stage={stage} credit={credit} />
          </ResizablePanel>
          <ResizableHandle withHandle />
          <ResizablePanel
            defaultSize={420}
            minSize={340}
            maxSize="55%"
            groupResizeBehavior="preserve-pixel-size"
          >
            <Sidebar chat={chat} composer={composer} sidebarOverlay={sidebarOverlay} />
          </ResizablePanel>
        </ResizablePanelGroup>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <PlanRegion toolbar={toolbar} stage={stage} credit={credit} />
          <Sidebar
            chat={chat}
            composer={composer}
            sidebarOverlay={sidebarOverlay}
            expand={{ expanded: chatExpanded, onToggle: () => setChatExpanded((v) => !v) }}
            className={cn(
              "flex-none border-t shadow-[0_-1px_0_var(--border)] transition-[height] duration-200",
              chatExpanded ? "h-[75dvh]" : "h-[50dvh]"
            )}
          />
        </div>
      )}
    </div>
  )
}

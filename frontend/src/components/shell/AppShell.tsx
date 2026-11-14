import { useState, type ReactNode } from "react"
import { LanguageMenu } from "./LanguageMenu"
import { useUi } from "@/i18n/uiLang"
import { ChevronsDownIcon, ChevronsUpIcon } from "lucide-react"
import { Link } from "react-router"

import { Button } from "@/components/ui/button"

import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { SPLIT_QUERY } from "@/features/plan/shared"
import { useMediaQuery } from "@/hooks/use-media-query"
import { cn } from "@/lib/utils"

import { Wordmark } from "./Wordmark"

export { SPLIT_QUERY }

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
  /** Whether `sidebarOverlay` is currently active and should mark the underlying chat region `inert`. */
  sidebarOverlayOpen?: boolean
}

function AgentTitle() {
  return <>{useUi().tu("agent_title")}</>
}

function Header({ title, headerActions }: Pick<AppShellSlots, "title" | "headerActions">) {
  const { tu } = useUi()
  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-background px-4 pt-[env(safe-area-inset-top)]">
      <Link to="/app" aria-label={tu("shell_home")} className="-mx-1 flex h-10 shrink-0 items-center rounded-md px-1">
        <Wordmark compact />
      </Link>
      <div className="h-5 w-px shrink-0 bg-border" aria-hidden />
      <div className="min-w-0 flex-1 truncate text-sm text-muted-foreground" data-slot="title">
        {title}
      </div>
      <div className="flex shrink-0 items-center gap-2" data-slot="header-actions">
        {headerActions}
        <LanguageMenu />
      </div>
    </header>
  )
}

function PlanRegion({ toolbar, stage, credit }: Pick<AppShellSlots, "toolbar" | "stage" | "credit">) {
  const { tu } = useUi()
  return (
    <main
      id="main"
      tabIndex={-1}
      aria-label={tu("shell_region")}
      className="flex h-full min-h-0 min-w-0 flex-1 flex-col bg-muted/40 outline-none"
    >
      <div className="flex shrink-0 items-center gap-2 border-b bg-background px-3 py-2" data-slot="toolbar">
        {toolbar}
      </div>
      <div className="relative min-h-36 flex-1 overflow-hidden" data-slot="stage">
        {stage}
      </div>
      <div className="shrink-0 border-t bg-background px-3 py-1.5 text-xs text-muted-foreground" data-slot="credit">
        {credit}
      </div>
    </main>
  )
}

function Sidebar({
  chat,
  composer,
  sidebarOverlay,
  sidebarOverlayOpen,
  className,
  expand,
}: Pick<AppShellSlots, "chat" | "composer" | "sidebarOverlay" | "sidebarOverlayOpen"> & {
  className?: string
  expand?: { expanded: boolean; onToggle: () => void }
}) {
  const { tu } = useUi()
  const overlayActive = sidebarOverlayOpen ?? Boolean(sidebarOverlay)
  return (
    <aside aria-label={tu("agent_title")} className={cn("flex h-full min-h-0 min-w-0 flex-col bg-background", className)}>
      <div className="flex h-11 shrink-0 items-center justify-between border-b pr-1 pl-4">
        <h2 className="text-sm font-semibold">
          <AgentTitle />
        </h2>
        {expand ? (
          <Button
            variant="ghost"
            size="icon"
            aria-expanded={expand.expanded}
            aria-label={expand.expanded ? tu("shell_chat_collapse") : tu("shell_chat_expand")}
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
          aria-label={tu("shell_conversation")}
          tabIndex={0}
          inert={overlayActive || undefined}
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
  const { tu } = useUi()
  const wide = useMediaQuery(SPLIT_QUERY, true)
  const [chatExpanded, setChatExpanded] = useState(false)
  const { title, headerActions, toolbar, stage, credit, chat, composer, sidebarOverlay, sidebarOverlayOpen } = props

  return (
    <div className="flex h-dvh w-full flex-col overflow-hidden bg-background">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:inline-flex focus:min-h-10 focus:min-w-10 focus:items-center focus:rounded-md focus:bg-primary focus:px-3 focus:text-xs focus:font-semibold focus:text-primary-foreground focus:shadow-md focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        {tu("skip_to_main")}
      </a>
      <Header title={title} headerActions={headerActions} />
      {wide ? (
        <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
          <ResizablePanel minSize={320}>
            <PlanRegion toolbar={toolbar} stage={stage} credit={credit} />
          </ResizablePanel>
          <ResizableHandle withHandle />
          <ResizablePanel
            defaultSize={420}
            minSize={320}
            maxSize="55%"
            groupResizeBehavior="preserve-pixel-size"
          >
            <Sidebar
              chat={chat}
              composer={composer}
              sidebarOverlay={sidebarOverlay}
              sidebarOverlayOpen={sidebarOverlayOpen}
            />
          </ResizablePanel>
        </ResizablePanelGroup>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <PlanRegion toolbar={toolbar} stage={stage} credit={credit} />
          <Sidebar
            chat={chat}
            composer={composer}
            sidebarOverlay={sidebarOverlay}
            sidebarOverlayOpen={sidebarOverlayOpen}
            expand={{ expanded: chatExpanded, onToggle: () => setChatExpanded((v) => !v) }}
            className={cn(
              "max-h-[calc(100%-13rem)] flex-none border-t shadow-[0_-1px_0_var(--border)] transition-[height] duration-200 motion-reduce:transition-none",
              chatExpanded ? "h-[75dvh]" : "h-[58dvh]"
            )}
          />
        </div>
      )}
    </div>
  )
}

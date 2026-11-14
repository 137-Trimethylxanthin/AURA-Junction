import { Suspense, lazy, useEffect } from "react"
import { useUi } from "@/i18n/uiLang"
import { Link, useParams } from "react-router"
import { AlertTriangleIcon, CloudOffIcon, FileQuestionIcon } from "lucide-react"
import { ApiError, getDrawingStatus } from "@/api/client"
import { apiBase, myProjects } from "@/api/library"
import { AppShell } from "@/components/shell"
import { SiteHeader } from "@/components/shell/SiteHeader"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Progress } from "@/components/ui/progress"
import { Spinner } from "@/components/ui/spinner"
import { TooltipProvider } from "@/components/ui/tooltip"
import {
  ChatPanel,
  Composer,
  DrawingTitle,
  HeaderActions,
  SidebarOverlay,
} from "@/features/chat"
import { SessionHistorySheet } from "@/features/history/SessionHistorySheet"
import { VoiceCopilotBar } from "@/features/voice/VoiceCopilotBar"
import { Credit, PlanStage, PlanToolbar } from "@/features/plan"
import { CommandPaletteDialog } from "@/features/shortcuts/CommandPaletteDialog"
import { ShortcutHelpDialog } from "@/features/shortcuts/ShortcutHelpDialog"
import {
  hydrateActiveProject,
  useWorkspaceShortcuts,
} from "@/features/shortcuts/useWorkspaceShortcuts"
import { useTitle } from "@/hooks/use-title"
import { BUNDLED_DRAWING_ID } from "@/data/ids"
import { SAMPLE_ROUTE } from "@/api/sample"
import {
  actions,
  POLL_DEADLINE_MS,
  selectDrawing,
  useAppStore,
  type DrawingLoad,
} from "@/store"

/** Set once the stored projects were checked for expired floors on this page load. */
let prunedThisPage = false

// three.js is only fetched when someone opens the 3D view.
const Viewer3D = lazy(() => import("@/features/viewer3d/Viewer3D"))

/** /d/:drawingId: the plan + chat workspace for one drawing. */
export default function Workspace() {
  const { drawingId = BUNDLED_DRAWING_ID } = useParams()
  const load = useAppStore((s) => s.session.load)
  const drawing = useAppStore(selectDrawing)
  const view = useAppStore((s) => s.ui.view)

  useEffect(() => {
    void actions().initSource()
    // Warm the code-split tools once the plan has painted, so the first use does not wait on the network.
    const warm = () => {
      void import("@/features/plan/CompareLayer")
      void import("@/features/plan/WhatIf")
      void import("@/features/trace/TracePanel")
      void import("@/features/plugins/PluginsSheet")
    }
    const id =
      "requestIdleCallback" in window
        ? window.requestIdleCallback(warm, { timeout: 2000 })
        : setTimeout(warm, 800)
    return () => {
      if ("cancelIdleCallback" in window)
        window.cancelIdleCallback(id as number)
      else clearTimeout(id)
    }
  }, [])
  useEffect(() => {
    // Prime local project membership BEFORE `loadDrawing` so same-project floor switches keep chat turns & cached drawings.
    const saved = myProjects.forDrawing(drawingId)
    if (saved && saved.floors.length >= 2) actions().setActiveProject(saved)
    void actions().loadDrawing(drawingId)
    return () => actions().stopLoading()
  }, [drawingId])

  // Switching to a cached floor of the same building swaps in place: for the one render between the URL change
  // and the store swap, keep the workspace (and the 3D scene) mounted instead of flashing the loading screen.
  const siblingSwitch = useAppStore((s) => {
    const floors = s.activeProject?.floors ?? []
    return (
      s.session.load.state === "ready" &&
      !!s.drawingById[drawingId] &&
      floors.some((f) => f.drawing_id === drawingId) &&
      floors.some((f) => f.drawing_id === s.session.drawing.id)
    )
  })
  const ready =
    (load.id === drawingId && load.state === "ready" && drawing.id === drawingId) || siblingSwitch
  const readyDrawingId = ready ? drawing.id : null
  useTitle(ready ? drawing.title : "Drawing")
  const sourceKind = useAppStore((s) => (s.session.source.state === 'ready' ? s.session.source.kind : null))
  const traceOpen = useAppStore((s) => s.ui.traceOpen)
  const activeProjectId = useAppStore((s) => s.activeProject?.id ?? null)

  // Hydrate House / Project bundle context from localStorage (`aura.projects`), `/projects`, or sibling plans.
  useEffect(() => {
    let controller = new AbortController()
    const sync = () => {
      controller.abort()
      controller = new AbortController()
      void hydrateActiveProject({
        drawingId,
        // Read at call time: depending on the drawing object re-fetched /projects and /drawings on every change.
        drawing: ready ? actions().session.drawing : undefined,
        sourceKind,
        signal: controller.signal,
      })
    }
    sync()
    const unsub = myProjects.subscribe(sync)
    return () => {
      controller.abort()
      unsub()
    }
  }, [drawingId, ready, readyDrawingId, sourceKind])

  // Expired uploads (404 after 24 h) leave dead floor pills: prune them once per page load, and at once
  // when the open drawing itself is gone.
  useEffect(() => {
    if (sourceKind !== "api" || prunedThisPage) return
    const base = apiBase()
    if (!base) return
    prunedThisPage = true
    void myProjects.pruneExpired(async (id) => {
      if (id === BUNDLED_DRAWING_ID) return false
      try {
        await getDrawingStatus({ baseUrl: base }, id)
        return false
      } catch (e) {
        return e instanceof ApiError && e.status === 404
      }
    })
  }, [sourceKind])
  useEffect(() => {
    if (load.id === drawingId && load.state === "not_found") myProjects.pruneFloors([drawingId])
  }, [load.id, load.state, drawingId])

  // After a reload: bring back this drawing's (or hydrated House Project's) last conversation from the API.
  useEffect(() => {
    if (ready && sourceKind === 'api') void actions().restoreConversation()
  }, [ready, sourceKind, drawingId, activeProjectId])

  if (!ready)
    return (
      <LoadState
        load={
          load.id === drawingId ? load : { id: drawingId, state: "loading" }
        }
      />
    )
  return (
    <TooltipProvider delayDuration={400}>
      <WorkspaceShortcutsBinding />
      <AppShell
        title={<DrawingTitle />}
        headerActions={<HeaderActions />}
        toolbar={<PlanToolbar />}
        stage={
          view === "3d" ? (
            <Suspense
              fallback={
                <div className="flex h-full items-center justify-center">
                  <Spinner />
                </div>
              }
            >
              <Viewer3D />
            </Suspense>
          ) : (
            <PlanStage />
          )
        }
        credit={<Credit />}
        chat={<ChatPanel />}
        composer={
          <div className="flex flex-col gap-2">
            <VoiceCopilotBar />
            <Composer />
          </div>
        }
        sidebarOverlay={<SidebarOverlay />}
        sidebarOverlayOpen={traceOpen}
      />
      <CommandPaletteDialog />
      <ShortcutHelpDialog />
      <SessionHistorySheet />
    </TooltipProvider>
  )
}

function WorkspaceShortcutsBinding() {
  useWorkspaceShortcuts()
  return null
}

function LoadState({ load }: { load: DrawingLoad }) {
  const { tu } = useUi()
  const pct =
    load.progress !== undefined
      ? Math.round(Math.min(1, Math.max(0, load.progress)) * 100)
      : null
  let body: React.ReactNode
  switch (load.state) {
    case "loading":
      body = (
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <Spinner />
          </EmptyMedia>
          <EmptyTitle>
            <h1>{tu('opening')}</h1>
          </EmptyTitle>
        </EmptyHeader>
      )
      break
    case "processing":
      body = (
        <>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Spinner />
            </EmptyMedia>
            <EmptyTitle>
              <h1>{tu('reading')}</h1>
            </EmptyTitle>
            <EmptyDescription>
              {tu('reading_text')}
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            {pct !== null ? (
              <div className="flex w-64 items-center gap-3">
                <Progress value={pct} aria-label={tu('processing_progress')} />
                <span className="num w-10 text-right text-xs text-muted-foreground">
                  {pct} %
                </span>
              </div>
            ) : null}
          </EmptyContent>
        </>
      )
      break
    default: {
      const copy: Record<
        string,
        { icon: React.ReactNode; title: string; text: string }
      > = {
        failed: {
          icon: <AlertTriangleIcon />,
          title: tu('load_failed'),
          text: load.message ?? tu('load_failed_text'),
        },
        too_slow: {
          icon: <AlertTriangleIcon />,
          title: tu('load_slow'),
          text: tu('load_slow_text', { min: POLL_DEADLINE_MS / 60_000 }),
        },
        not_found: {
          icon: <FileQuestionIcon />,
          title: tu('load_not_found'),
          text: tu('load_not_found_text'),
        },
        unavailable: {
          icon: <CloudOffIcon />,
          title: tu('load_unavailable'),
          text: tu('load_unavailable_text'),
        },
        error: {
          icon: <AlertTriangleIcon />,
          title: tu('load_error'),
          text: load.message ?? tu('load_error_text'),
        },
      }
      const c = copy[load.state] ?? copy.error
      body = (
        <>
          <EmptyHeader>
            <EmptyMedia variant="icon">{c.icon}</EmptyMedia>
            <EmptyTitle>
              <h1>{c.title}</h1>
            </EmptyTitle>
            <EmptyDescription>{c.text}</EmptyDescription>
          </EmptyHeader>
          <EmptyContent className="flex-row justify-center gap-2">
            <Button asChild>
              <Link to="/app">{tu('all_drawings')}</Link>
            </Button>
            <Button variant="outline" asChild>
              <Link to={SAMPLE_ROUTE}>
                {tu('open_sample')}
              </Link>
            </Button>
          </EmptyContent>
        </>
      )
    }
  }
  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <SiteHeader />
      <main
        id="main"
        tabIndex={-1}
        className="flex flex-1 items-center justify-center p-6 outline-none"
        aria-live="polite"
        data-drawing-load={load.state}
      >
        <Empty>{body}</Empty>
      </main>
    </div>
  )
}

import { useEffect } from "react"
import { useUi } from "@/i18n/uiLang"
import { Link, useParams } from "react-router"
import { AlertTriangleIcon, CloudOffIcon, FileQuestionIcon } from "lucide-react"
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
import { Credit, PlanStage, PlanToolbar } from "@/features/plan"
import { useTitle } from "@/hooks/use-title"
import { BUNDLED_DRAWING_ID } from "@/data/ids"
import {
  actions,
  POLL_DEADLINE_MS,
  selectDrawing,
  useAppStore,
  type DrawingLoad,
} from "@/store"

/** /d/:drawingId: the plan + chat workspace for one drawing. */
export default function Workspace() {
  const { drawingId = BUNDLED_DRAWING_ID } = useParams()
  const load = useAppStore((s) => s.session.load)
  const drawing = useAppStore(selectDrawing)

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
    void actions().loadDrawing(drawingId)
    return () => actions().stopLoading()
  }, [drawingId])

  const ready =
    load.id === drawingId && load.state === "ready" && drawing.id === drawingId
  useTitle(ready ? drawing.title : "Drawing")
  const sourceKind = useAppStore((s) => (s.session.source.state === 'ready' ? s.session.source.kind : null))
  // After a reload: bring back this drawing's last conversation from the API (kept by id in localStorage).
  useEffect(() => {
    if (ready && sourceKind === 'api') void actions().restoreConversation()
  }, [ready, sourceKind, drawingId])

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
      <AppShell
        title={<DrawingTitle />}
        headerActions={<HeaderActions />}
        toolbar={<PlanToolbar />}
        stage={<PlanStage />}
        credit={<Credit />}
        chat={<ChatPanel />}
        composer={<Composer />}
        sidebarOverlay={<SidebarOverlay />}
      />
    </TooltipProvider>
  )
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
              <Link to={`/d/${BUNDLED_DRAWING_ID}`}>
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
        className="flex flex-1 items-center justify-center p-6"
        aria-live="polite"
        data-drawing-load={load.state}
      >
        <Empty>{body}</Empty>
      </main>
    </div>
  )
}

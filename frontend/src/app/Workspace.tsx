import { useEffect } from 'react'
import { Link, useParams } from 'react-router'
import { AlertTriangleIcon, CloudOffIcon, FileQuestionIcon } from 'lucide-react'
import { AppShell } from '@/components/shell'
import { SiteHeader } from '@/components/shell/SiteHeader'
import { Button } from '@/components/ui/button'
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Progress } from '@/components/ui/progress'
import { Spinner } from '@/components/ui/spinner'
import { ChatPanel, Composer, DrawingTitle, HeaderActions, SidebarOverlay } from '@/features/chat'
import { Credit, PlanStage, PlanToolbar } from '@/features/plan'
import { useTitle } from '@/hooks/use-title'
import { BUNDLED_DRAWING_ID } from '@/data/ids'
import { actions, selectDrawing, useAppStore, type DrawingLoad } from '@/store'

/** /d/:drawingId: the plan + chat workspace for one drawing. */
export default function Workspace() {
  const { drawingId = BUNDLED_DRAWING_ID } = useParams()
  const load = useAppStore((s) => s.session.load)
  const drawing = useAppStore(selectDrawing)

  useEffect(() => {
    void actions().initSource()
  }, [])
  useEffect(() => {
    void actions().loadDrawing(drawingId)
    return () => actions().stopLoading()
  }, [drawingId])

  const ready = load.id === drawingId && load.state === 'ready' && drawing.id === drawingId
  useTitle(ready ? drawing.title : 'Drawing')

  if (!ready) return <LoadState load={load.id === drawingId ? load : { id: drawingId, state: 'loading' }} />
  return (
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
  )
}

function LoadState({ load }: { load: DrawingLoad }) {
  const pct = load.progress !== undefined ? Math.round(Math.min(1, Math.max(0, load.progress)) * 100) : null
  let body: React.ReactNode
  switch (load.state) {
    case 'loading':
      body = (
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <Spinner />
          </EmptyMedia>
          <EmptyTitle>
            <h1>Opening the drawing</h1>
          </EmptyTitle>
        </EmptyHeader>
      )
      break
    case 'processing':
      body = (
        <>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Spinner />
            </EmptyMedia>
            <EmptyTitle>
              <h1>Reading the drawing</h1>
            </EmptyTitle>
            <EmptyDescription>
              The server is extracting rooms and objects. This page opens the drawing as soon as it is ready.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            {pct !== null ? (
              <div className="flex w-64 items-center gap-3">
                <Progress value={pct} aria-label="Processing progress" />
                <span className="num w-10 text-right text-xs text-muted-foreground">{pct} %</span>
              </div>
            ) : null}
          </EmptyContent>
        </>
      )
      break
    default: {
      const copy: Record<string, { icon: React.ReactNode; title: string; text: string }> = {
        failed: {
          icon: <AlertTriangleIcon />,
          title: 'This drawing could not be read',
          text: load.message ?? 'The server could not process the file.',
        },
        not_found: {
          icon: <FileQuestionIcon />,
          title: 'Drawing not found',
          text: 'It may have expired: uploads are deleted after 24 hours.',
        },
        unavailable: {
          icon: <CloudOffIcon />,
          title: 'This drawing needs the AURA server',
          text: 'The server is not connected right now, so only the bundled sample drawing can be opened.',
        },
        error: {
          icon: <AlertTriangleIcon />,
          title: 'The drawing could not be loaded',
          text: load.message ?? 'Try again in a moment.',
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
              <Link to="/app">All drawings</Link>
            </Button>
            <Button variant="outline" asChild>
              <Link to={`/d/${BUNDLED_DRAWING_ID}`}>Open the sample drawing</Link>
            </Button>
          </EmptyContent>
        </>
      )
    }
  }
  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <SiteHeader />
      <main className="flex flex-1 items-center justify-center p-6" aria-live="polite" data-drawing-load={load.state}>
        <Empty>{body}</Empty>
      </main>
    </div>
  )
}

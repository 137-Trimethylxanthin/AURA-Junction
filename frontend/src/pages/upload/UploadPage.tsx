import './upload.css'
import { useUi } from '@/i18n/uiLang'
import type { UiKey } from '@/i18n/strings'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import {
  ArrowLeftIcon,
  ChevronDownIcon,
  CloudOffIcon,
  FileStackIcon,
  RotateCwIcon,
  Trash2Icon,
  TriangleAlertIcon,
} from 'lucide-react'
import { apiBase } from '@/api/library'
import { useTitle } from '@/hooks/use-title'
import type { UploadPage as UploadPageInfo } from '@/api/contract'
import { SiteHeader } from '@/components/shell/SiteHeader'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { useDraft } from '@/features/chat/draft'
import { LOCALE } from '@/lib/format'
import { cn } from '@/lib/utils'
import { StageTimeline } from './StageTimeline'
import { elapsed, estimatedCounts as estimatesOf, realFraction, reportedCounts, stageView, type StageView } from './stages'
import {
  UPLOAD_BATCH,
  acceptAttr,
  uploadHandoff,
  useAccepted,
  type Accepted,
  useHasPresenter,
  useStoreMode,
  useUploads,
  type UploadRow,
} from './useUploads'

const MB = 1024 * 1024

/** /upload and /upload/:drawingId: send drawings and watch each one (and each PDF page) move through the pipeline. */
export default function UploadPage() {
  const { tu } = useUi()
  const base = apiBase()
  useTitle(tu('ul_title'))
  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <SiteHeader />
      <main className="page-enter mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
        {base ? (
          <Uploads base={base} />
        ) : (
          <>
            <PageHead mode={null} />
            <Alert data-uploads-off>
              <CloudOffIcon />
              <AlertTitle>{tu('up_off_title')}</AlertTitle>
              <AlertDescription>{tu('up_off_desc')}</AlertDescription>
            </Alert>
          </>
        )}
      </main>
    </div>
  )
}

function PageHead({ mode }: { mode: 'full' | 'quick' | null }) {
  const { tu } = useUi()
  return (
    <div className="flex flex-col gap-3">
      <Button variant="ghost" asChild className="-ml-3 self-start text-muted-foreground">
        <Link to="/app">
          <ArrowLeftIcon data-icon="inline-start" />
          {tu('ul_back')}
        </Link>
      </Button>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="text-xl font-semibold tracking-tight">{tu('ul_title')}</h1>
        {mode ? <ModeBadge mode={mode} /> : null}
      </div>
      <p className="max-w-prose text-sm text-muted-foreground">
        {tu(mode === 'quick' ? 'ul_intro_quick' : 'ul_intro')}
      </p>
    </div>
  )
}

function ModeBadge({ mode }: { mode: 'full' | 'quick' }) {
  const { tu } = useUi()
  return (
    <Badge
      variant="outline"
      data-mode={mode}
      title={tu(mode === 'full' ? 'ul_mode_full_tip' : 'ul_mode_quick_tip')}
      className="h-6 gap-1.5 px-2.5"
    >
      <span aria-hidden className={cn('size-1.5 rounded-full', mode === 'full' ? 'bg-success' : 'bg-warning')} />
      {tu(mode === 'full' ? 'ul_mode_full' : 'ul_mode_quick')}
    </Badge>
  )
}

/** Re-renders every second while something is still running (for elapsed times). */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [active])
  return now
}

function viewOf(r: UploadRow, mode: 'full' | 'quick' | null): StageView {
  return stageView(r.phase, r.status?.stage, { mode: r.status?.mode ?? mode ?? undefined, last: r.last, slow: r.slow })
}

function Uploads({ base }: { base: string }) {
  const { tu, tn } = useUi()
  const { drawingId } = useParams()
  const mode = useStoreMode(base)
  const { rows, rejected, add, remove, retry, watch } = useUploads(base, { mode })
  const hasPresenter = useHasPresenter()
  const accepted = useAccepted(base)
  const anyLive = rows.some((r) => viewOf(r, mode).outcome === 'working')
  const now = useNow(anyLive)

  // Files dropped on the library card arrive here.
  useEffect(() => {
    const files = uploadHandoff.take()
    if (files.length) void add(files, hasPresenter, accepted)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (drawingId) watch(drawingId)
  }, [drawingId, watch])

  const effectiveMode = rows.find((r) => r.status?.mode)?.status?.mode ?? mode

  return (
    <>
      <PageHead mode={effectiveMode} />
      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <aside className="flex min-w-0 flex-col gap-4 lg:sticky lg:top-6">
          <DropZone onFiles={(f) => void add(f, hasPresenter, accepted)} accepted={accepted} />
          {rejected.length ? (
            <Alert variant="destructive" data-upload-rejected>
              <TriangleAlertIcon />
              <AlertTitle>{tn('up_rejected', rejected.reduce((k, x) => k + (x.n ?? 1), 0))}</AlertTitle>
              <AlertDescription className="min-w-0 [overflow-wrap:anywhere]">
                <ul className="flex flex-col gap-1">
                  {rejected.map((r, i) => (
                    <li key={i} className="line-clamp-3">
                      {r.n ? (
                        <>
                          {tn('up_batch', r.n, { max: UPLOAD_BATCH })} <b className="font-medium break-all">{r.name}</b>
                        </>
                      ) : (
                        <>
                          <b className="font-medium break-all">{r.name}</b>: {r.reason}
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          ) : null}
        </aside>

        <section aria-labelledby="uploads-title" className="flex min-w-0 flex-col gap-3">
          <h2 id="uploads-title" className="flex min-h-10 items-center text-sm font-medium">
            {tu('your_uploads')}
            {rows.length ? <span className="num ml-2 font-normal text-muted-foreground">{rows.length}</span> : null}
          </h2>
          {rows.length === 0 ? (
            <Empty className="rounded-xl border border-dashed py-12" data-uploads-empty>
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <FileStackIcon />
                </EmptyMedia>
                <EmptyTitle>{tu('ul_empty_title')}</EmptyTitle>
                <EmptyDescription>{tu('ul_empty_desc')}</EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            <ul className="flex flex-col gap-3">
              {rows.map((r) => (
                <li key={r.uid ?? r.id} className={r.leaving ? 'exit-fall' : 'enter-rise'} data-upload={r.phase}>
                  <UploadCard
                    r={r}
                    view={viewOf(r, effectiveMode)}
                    mode={effectiveMode}
                    now={now}
                    focused={r.id === drawingId}
                    onDelete={() => void remove(r)}
                    onRetry={(reindex) => void retry(r, reindex)}
                  />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </>
  )
}

function DropZone({ onFiles, accepted }: { onFiles: (f: FileList | null) => void; accepted: Accepted }) {
  const { tu } = useUi()
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  return (
    <Card className="gap-0 p-0">
      <div
        data-dropzone
        data-over={over || undefined}
        onDragOver={(e) => {
          e.preventDefault()
          e.dataTransfer.dropEffect = 'copy'
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          onFiles(e.dataTransfer.files)
        }}
        className={cn(
          'ul-drop m-3 flex flex-col items-center gap-3 rounded-lg border border-dashed border-input px-4 py-8 text-center',
          over && 'border-foreground bg-muted',
        )}
      >
        <span className="ul-drop-glyph text-muted-foreground">
          <SheetGlyph state="idle" />
        </span>
        <div className="flex flex-col gap-1">
          <p className="text-sm font-medium text-foreground">{tu('drop_here')}</p>
          <p className="text-xs text-muted-foreground">{tu('ul_batch_hint', { n: UPLOAD_BATCH })}</p>
        </div>
        <Button onClick={() => input.current?.click()}>{tu('choose_file')}</Button>
        <input
          ref={input}
          type="file"
          multiple
          accept={acceptAttr(accepted)}
          className="sr-only"
          tabIndex={-1}
          aria-label={tu('up_file_aria')}
          data-upload-input
          onChange={(e) => {
            onFiles(e.currentTarget.files)
            e.currentTarget.value = ''
          }}
        />
      </div>
      <ul className="flex flex-col gap-2 border-t px-4 py-3 text-xs text-muted-foreground" aria-label={tu('ul_types')} data-accepted={accepted.fromServer ? 'server' : 'builtin'}>
        {accepted.types.map((t) => (
          <li key={t.type} className="num">
            {tu(t.presenter_only ? 'ul_cap_presenter' : 'ul_cap', { type: t.type, mb: t.max_mb })}
          </li>
        ))}
      </ul>
      <div className="flex flex-col gap-1 border-t px-4 py-3 text-xs text-muted-foreground">
        <p>{tu('ul_pdf_pages')}</p>
        <p>
          {tu('ul_deleted_ttl', { h: accepted.ttlHours })}{' '}
          <Link to="/privacy" className="font-medium text-foreground underline underline-offset-2">
            {tu('nav_privacy')}
          </Link>
        </p>
      </div>
    </Card>
  )
}

/** A drawing sheet: idle, bytes lifting (uploading), a scan line (being read), or a drawn tick (done). */
function SheetGlyph({ state }: { state: 'idle' | 'uploading' | 'reading' | 'done' | 'problem' }) {
  return (
    <svg viewBox="0 0 32 32" className="size-8" fill="none" stroke="currentColor" strokeWidth={1.5} aria-hidden>
      <path d="M8 3.5h11l5.5 5.5v19.5H8z" strokeLinejoin="round" />
      <path d="M19 3.5V9h5.5" strokeLinejoin="round" />
      {state === 'idle' || state === 'reading' ? (
        <g opacity={0.45}>
          <path d="M11.5 14h9M11.5 18h5.5M11.5 22h7.5" strokeLinecap="round" />
        </g>
      ) : null}
      {state === 'reading' ? <path className="ul-sheet-scan" d="M10 13h12.5" strokeWidth={2} strokeLinecap="round" /> : null}
      {state === 'uploading' ? (
        <g className="ul-sheet-lift">
          <path d="M16 24v-9M12.5 18.5 16 15l3.5 3.5" strokeLinecap="round" strokeLinejoin="round" />
        </g>
      ) : null}
      {state === 'done' ? <path className="ul-sheet-tick" d="M11.5 18.5l3 3 6-6.5" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" /> : null}
      {state === 'problem' ? <path d="M16 14.5v5M16 23v.01" strokeWidth={2} strokeLinecap="round" /> : null}
    </svg>
  )
}

const OUTCOME_BADGE: Record<StageView['outcome'], { key: UiKey; cls: string }> = {
  working: { key: 'ul_o_working', cls: 'border-border text-muted-foreground' },
  done: { key: 'ul_o_done', cls: 'border-success/40 text-success' },
  quick: { key: 'ul_o_quick', cls: 'border-warning/40 text-warning' },
  partial: { key: 'ul_o_partial', cls: 'border-warning/40 text-warning' },
  store_failed: { key: 'ul_o_store_failed', cls: 'border-destructive/40 text-destructive' },
  failed: { key: 'ul_o_failed', cls: 'border-destructive/40 text-destructive' },
  too_slow: { key: 'ul_o_too_slow', cls: 'border-warning/40 text-warning' },
  expired: { key: 'phase_expired', cls: 'border-border text-muted-foreground' },
}

const SUGGEST: Record<'cad' | 'image', UiKey[]> = {
  cad: ['ul_q_rooms', 'ul_q_area', 'ul_q_doors'],
  image: ['ul_q_rooms', 'ul_q_windows', 'ul_q_largest'],
}

const COUNT_LABEL: Record<string, UiKey> = {
  apartments: 'ul_c_apartments',
  texts: 'ul_c_texts',
  rooms: 'ul_c_rooms',
  doors: 'ul_c_doors',
  windows: 'ul_c_windows',
  fixtures: 'ul_c_fixtures',
}

function kindKey(name: string): UiKey {
  const n = name.toLowerCase()
  if (n.endsWith('.pdf')) return 'kind_pdf'
  if (n.endsWith('.dxf')) return 'ul_kind_dxf'
  if (n.endsWith('.ifc')) return 'ul_kind_ifc'
  return 'kind_image'
}

function UploadCard({
  r,
  view,
  mode,
  now,
  focused,
  onDelete,
  onRetry,
}: {
  r: UploadRow
  view: StageView
  mode: 'full' | 'quick' | null
  now: number
  focused: boolean
  onDelete(): void
  onRetry(reindex?: boolean): void
}) {
  const { tu, lang } = useUi()
  const navigate = useNavigate()
  const ref = useRef<HTMLDivElement>(null)
  const st = r.status
  const live = view.outcome === 'working'
  const quick = view.outcome === 'quick' || (mode === 'quick' && view.outcome === 'done')
  const fraction = r.phase === 'uploading' ? realFraction(r.sent) : realFraction(st?.progress)
  const glyph =
    r.phase === 'uploading'
      ? 'uploading'
      : live
        ? 'reading'
        : view.outcome === 'failed' || view.outcome === 'store_failed'
          ? 'problem'
          : view.outcome === 'done' || view.outcome === 'quick'
            ? 'done'
            : 'idle'
  const badge = OUTCOME_BADGE[view.outcome]
  const t = elapsed(live && st?.elapsed_s !== undefined && r.phase !== 'uploading' ? st.elapsed_s * 1000 : now - Date.parse(r.created_at))
  const counts = reportedCounts(st?.counts)
  const estimates = estimatesOf(st)
  const pages = st?.pages ?? []
  const isCad = /\.(dxf|ifc)$/i.test(r.name)
  const openable = view.openable && r.phase === 'ready'

  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [focused])

  const ask = (q: string) => {
    useDraft.getState().fill(q)
    navigate(`/d/${encodeURIComponent(r.id)}`)
  }

  return (
    <Card ref={ref} size="sm" className={cn('gap-3 px-4', focused && 'ul-focus')} data-outcome={view.outcome}>
      <div className="flex min-w-0 items-start gap-3">
        <span
          className={cn(
            'grid size-11 shrink-0 place-items-center rounded-lg bg-muted',
            glyph === 'problem' ? 'text-destructive' : glyph === 'done' ? 'text-foreground' : 'text-muted-foreground',
          )}
        >
          <SheetGlyph state={glyph} />
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <h3 className="truncate text-sm font-medium" title={r.name}>
            {r.name}
          </h3>
          <p className="num flex flex-wrap gap-x-2 text-xs text-muted-foreground">
            <span>{tu(kindKey(r.name))}</span>
            {r.size ? <span>{tu('ul_size_mb', { mb: (r.size / MB).toFixed(r.size < MB ? 2 : 1) })}</span> : null}
            {live ? (
              <span>{tu('ul_elapsed', { m: t.m, s: String(t.s).padStart(2, '0') })}</span>
            ) : Number.isFinite(Date.parse(r.created_at)) ? (
              <span>
                {tu('ul_uploaded_at', {
                  time: new Date(r.created_at).toLocaleTimeString(LOCALE[lang], { hour: '2-digit', minute: '2-digit' }),
                })}
              </span>
            ) : null}
          </p>
        </div>
        <Badge variant="outline" className={cn('shrink-0', badge.cls)}>
          {tu(badge.key)}
        </Badge>
      </div>

      <StageTimeline view={view} fraction={live ? fraction : null} quickSkips={quick} stageSeconds={live ? st?.stage_s : undefined} />

      {st?.note && (live || view.outcome === 'done') ? <p className="text-xs text-muted-foreground">{st.note}</p> : null}
      {live && openable ? <p className="text-xs text-muted-foreground">{tu('ul_still_reading')}</p> : null}

      <Callout r={r} view={view} onRetry={onRetry} />

      {counts.length ? (
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-live="polite" data-upload-counts>
          {counts.map((c) => (
            <div key={c.key} className="flex min-w-0 flex-col rounded-lg bg-muted/60 px-3 py-2">
              <dd className="num text-lg leading-tight font-semibold tabular-nums">{c.n}</dd>
              <dt className="truncate text-xs text-muted-foreground">
                {tu(COUNT_LABEL[c.key])}
                {estimates.has(c.key) ? ` ${tu('ul_estimate')}` : ''}
              </dt>
            </div>
          ))}
        </dl>
      ) : null}

      {pages.length > 1 ? <Pages pages={pages} parent={r} mode={mode} /> : null}

      {st?.ingest_calls !== undefined ? (
        <p className="num text-xs text-muted-foreground" data-ingest>
          {st.ingest_cost_usd !== undefined
            ? tu('ul_ingest', { n: st.ingest_calls, cost: `$${st.ingest_cost_usd.toFixed(3)}` })
            : tu('ul_ingest_calls', { n: st.ingest_calls })}
        </p>
      ) : null}

      {r.pollError && live ? <p className="text-xs text-muted-foreground">{r.pollError}</p> : null}

      <div className="flex flex-wrap items-center gap-2">
        {openable ? (
          <Button asChild>
            <Link to={`/d/${encodeURIComponent(r.id)}`}>{tu(live ? 'ul_open_now' : 'open')}</Link>
          </Button>
        ) : null}
        {openable
          ? SUGGEST[isCad ? 'cad' : 'image'].map((k) => (
              <Button key={k} variant="outline" className="max-w-full" onClick={() => ask(tu(k))}>
                <span className="truncate">{tu(k)}</span>
              </Button>
            ))
          : null}
        {r.delete_token ? (
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="ghost" size="icon" className="ml-auto" aria-label={tu('up_delete_aria', { name: r.name })} title={tu('up_delete_tip')}>
                <Trash2Icon />
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{tu('up_delete_title', { name: r.name })}</AlertDialogTitle>
                <AlertDialogDescription>{tu('up_delete_desc')}</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>{tu('up_keep')}</AlertDialogCancel>
                <AlertDialogAction variant="destructive" onClick={onDelete}>
                  {tu('up_delete')}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        ) : null}
      </div>
    </Card>
  )
}

/** What happened, in words, for every outcome that is not plain progress or success. */
function Callout({ r, view, onRetry }: { r: UploadRow; view: StageView; onRetry(reindex?: boolean): void }) {
  const { tu } = useUi()
  const note = r.status?.note
  const error = r.localError ?? r.status?.error?.replace(/^(DXF |The file |This file )?could not be read:?\s*/i, '')
  switch (view.outcome) {
    case 'store_failed':
      return (
        <Alert variant="destructive" data-callout="store_failed">
          <TriangleAlertIcon />
          <AlertTitle>{tu('ul_store_failed_title')}</AlertTitle>
          <AlertDescription className="flex flex-col items-start gap-2">
            <span>{note || tu('ul_store_failed_desc')}</span>
            {view.openable ? <span>{tu('ul_still_askable')}</span> : null}
            <Button variant="outline" onClick={() => onRetry(true)}>
              <RotateCwIcon data-icon="inline-start" />
              {tu('ul_retry')}
            </Button>
          </AlertDescription>
        </Alert>
      )
    case 'partial':
      return (
        <Alert data-callout="partial">
          <TriangleAlertIcon className="text-warning" />
          <AlertTitle>{tu('ul_partial_title')}</AlertTitle>
          <AlertDescription className="flex flex-col items-start gap-2">
            <span>{note || tu('ul_partial_desc')}</span>
            {r.delete_token ? (
              <Button variant="outline" onClick={() => onRetry(true)}>
                <RotateCwIcon data-icon="inline-start" />
                {tu('ul_retry')}
              </Button>
            ) : null}
          </AlertDescription>
        </Alert>
      )
    case 'quick':
      return (
        <Alert data-callout="quick">
          <TriangleAlertIcon className="text-warning" />
          <AlertTitle>{tu('ul_quick_title')}</AlertTitle>
          <AlertDescription>{tu('ul_quick_desc')}</AlertDescription>
        </Alert>
      )
    case 'too_slow':
      return (
        <Alert data-callout="too_slow">
          <TriangleAlertIcon className="text-warning" />
          <AlertTitle>{tu('ul_slow_title')}</AlertTitle>
          <AlertDescription className="flex flex-col items-start gap-2">
            <span>{tu(view.openable ? 'ul_slow_desc_open' : 'ul_slow_desc')}</span>
            <Button variant="outline" onClick={() => onRetry(false)}>
              <RotateCwIcon data-icon="inline-start" />
              {tu('ul_check_again')}
            </Button>
          </AlertDescription>
        </Alert>
      )
    case 'failed':
      return (
        <Alert variant="destructive" data-callout="failed">
          <TriangleAlertIcon />
          <AlertTitle>{tu('ul_failed_title')}</AlertTitle>
          <AlertDescription>{error || note || tu('ul_failed_desc')}</AlertDescription>
        </Alert>
      )
    default:
      return null
  }
}

function Pages({ pages, parent, mode }: { pages: UploadPageInfo[]; parent: UploadRow; mode: 'full' | 'quick' | null }) {
  const { tu } = useUi()
  const [open, setOpen] = useState(true)
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-lg border">
      <CollapsibleTrigger asChild>
        <button
          type="button"
          className="flex min-h-10 w-full items-center gap-2 px-3 text-left text-xs font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <ChevronDownIcon
            aria-hidden
            className={cn('size-4 shrink-0 transition-transform duration-200 ease-out', !open && '-rotate-90')}
          />
          <span className="flex-1">{tu('ul_pages', { n: pages.length })}</span>
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul className="flex flex-col divide-y border-t">
          {pages.map((p, i) => {
            const v = stageView(parent.phase, p.stage, { mode: parent.status?.mode ?? mode ?? undefined })
            const counts = reportedCounts(p.counts)
            const est = estimatesOf(parent.status, p.methods)
            const n = p.page ?? i + 1
            return (
              <li key={n} className="flex flex-col gap-1.5 px-3 py-2.5" data-page={n} data-outcome={v.outcome}>
                <div className="flex min-w-0 items-center gap-2 text-xs">
                  <span className="font-medium">
                    {tu('ul_page', { n })}
                    {p.label ? <span className="ml-1 text-muted-foreground">· {p.label}</span> : null}
                  </span>
                  <span className="truncate text-muted-foreground">
                    {v.outcome === 'working' ? tu(`ul_st_${v.step}` as UiKey) : tu(OUTCOME_BADGE[v.outcome].key)}
                  </span>
                  {p.drawing_id && v.openable ? (
                    <Link
                      to={`/d/${encodeURIComponent(p.drawing_id)}`}
                      className="ml-auto inline-flex min-h-10 items-center font-medium underline underline-offset-2"
                    >
                      {tu('open')}
                    </Link>
                  ) : null}
                </div>
                <StageTimeline view={v} fraction={v.outcome === 'working' ? realFraction(p.progress) : null} dense />
                {counts.length ? (
                  <p className="num text-xs text-muted-foreground">
                    {counts
                      .map((c) => `${c.n} ${tu(COUNT_LABEL[c.key]).toLowerCase()}${est.has(c.key) ? ` ${tu('ul_estimate')}` : ''}`)
                      .join(', ')}
                  </p>
                ) : null}
                {p.error ? <p className="text-xs text-destructive">{p.error}</p> : null}
              </li>
            )
          })}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  )
}

import { tu as tuNow, useUi } from "@/i18n/uiLang"
import type { UiKey } from "@/i18n/strings"
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react"
import { Link } from "react-router"
import { toast } from "sonner"
import {
  CloudOffIcon,
  FileCheckIcon,
  FileIcon,
  FileUpIcon,
  FileWarningIcon,
  KeyRoundIcon,
  LayersIcon,
  Trash2Icon,
  TriangleAlertIcon,
} from "lucide-react"
import { ApiError, deleteDrawing, getDrawingStatus } from "@/api/client"
import { isProjectBundle, type DrawingStatus } from "@/api/contract"
import {
  ACCEPT,
  UPLOAD_CAPS,
  checkFile,
  myProjects,
  myUploads,
  presenter,
  uploadErrorText,
  uploadFile,
  type StoredUpload,
} from "@/api/library"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
} from "@/components/ui/alert-dialog"
import {
  Attachment,
  AttachmentAction,
  AttachmentActions,
  AttachmentContent,
  AttachmentDescription,
  AttachmentMedia,
  AttachmentTitle,
} from "@/components/ui/attachment"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Spinner } from "@/components/ui/spinner"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { Progress } from "@/components/ui/progress"
import { cn } from "@/lib/utils"

/** How often an upload that is still being processed is polled. */
const UPLOAD_POLL_MS = 1500
/** An upload still queued or processing this long after it was sent is reported as stuck and no longer polled. */
const UPLOAD_DEADLINE_MS = 3 * 60_000
/** Files taken from one drop or pick; the rest are listed as not uploaded. */
const UPLOAD_BATCH = 5

export interface ReadyUploadSummary {
  id: string
  name: string
}

export interface UploadPanelProps {
  base: string | null
  onReadyUploadsChange?: (uploads: ReadyUploadSummary[]) => void
  onBundleUploads?: (uploadIds: string[]) => void
  onProjectsDiscovered?: () => void
  onOpenUpload?: (uploadId: string) => void
}

type Row = StoredUpload & {
  phase: "uploading" | DrawingStatus | "expired" | "too_slow"
  /** 0–1: upload bytes while uploading, server progress while processing. */
  progress?: number
  error?: string
  /** Stable React key: the temporary id while uploading stays the key after the server id arrives. */
  uid?: string
  /** Being removed: plays the exit fade before it leaves the list. */
  leaving?: boolean
}

let uploadSeq = 0

/** Files named by the rejection lines (a batch overflow line names several files). */
const rejectedCount = (r: { n?: number }[]) => r.reduce((k, x) => k + (x.n ?? 1), 0)

/** Discover server-created multi-page PDF projects & sibling drawings once a PDF upload finishes processing. */
async function discoverServerProjects(base: string, onProjectsDiscovered?: () => void) {
  try {
    const resp = await fetch(`${base.replace(/\/+$/, "")}/projects`)
    if (resp.ok) {
      const body: unknown = await resp.json()
      const projects =
        body && typeof body === "object" && Array.isArray((body as { projects?: unknown }).projects)
          ? (body as { projects: unknown[] }).projects.filter(isProjectBundle)
          : []
      if (projects.length > 0) {
        myProjects.syncFromServer(projects)
      }
    }
  } catch {
    /* ignore network / 404 / storage errors */
  } finally {
    onProjectsDiscovered?.()
  }
}

/** The user's own uploads: stored ids + delete tokens, with live status from the API. */
function useUploads(base: string, onProjectsDiscovered?: () => void) {
  const [rows, setRows] = useState<Row[]>(() =>
    myUploads.list().map((u) => ({ ...u, phase: "queued" as const }))
  )
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const alive = useRef(true)
  /** Deleted here: a status poll still in flight must not re-arm its timer. */
  const removed = useRef(new Set<string>())

  const patch = useCallback((id: string, p: Partial<Row>) => {
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...p } : r)))
  }, [])

  const poll = useCallback(
    (id: string, createdAt: string, fileName?: string) => {
      const sent = Date.parse(createdAt)
      const started = Date.now()
      // Still queued/processing this long after upload: stuck.
      const overdue = () =>
        Number.isFinite(sent) && Date.now() - sent > UPLOAD_DEADLINE_MS
      // Status calls failing: give up only after failing for this long in this session (a cold start is not "stuck").
      const failingTooLong = () => Date.now() - started > UPLOAD_DEADLINE_MS
      const tick = async () => {
        clearTimeout(timers.current.get(id))
        try {
          const st = await getDrawingStatus({ baseUrl: base }, id)
          if (!alive.current || removed.current.has(id)) return
          const pending = st.status === "queued" || st.status === "processing"
          if (pending && overdue()) {
            patch(id, { phase: "too_slow", progress: undefined, error: undefined })
            return
          }
          patch(id, {
            phase: st.status,
            progress: st.progress,
            error: st.error,
          })
          if (st.status === "ready" && fileName?.toLowerCase().endsWith(".pdf")) {
            void discoverServerProjects(base, onProjectsDiscovered)
          }
          if (pending)
            timers.current.set(
              id,
              setTimeout(() => void tick(), UPLOAD_POLL_MS)
            )
        } catch (e) {
          if (!alive.current || removed.current.has(id)) return
          if (
            e instanceof ApiError &&
            (e.code === "unknown_drawing" || e.status === 404)
          ) {
            // Expired (24 h) or deleted elsewhere: forget it.
            myUploads.remove(id)
            patch(id, { phase: "expired" })
            return
          }
          if (failingTooLong()) {
            patch(id, { phase: "too_slow", progress: undefined, error: undefined })
            return
          }
          patch(id, { error: uploadErrorText(e) })
          timers.current.set(
            id,
            setTimeout(() => void tick(), UPLOAD_POLL_MS * 4)
          )
        }
      }
      void tick()
    },
    [base, patch, onProjectsDiscovered]
  )

  useEffect(() => {
    alive.current = true
    const map = timers.current
    for (const u of myUploads.list()) poll(u.id, u.created_at, u.name)
    return () => {
      alive.current = false
      for (const t of map.values()) clearTimeout(t)
      map.clear()
    }
  }, [poll])

  const start = useCallback(
    async (file: File): Promise<string | null> => {
      const temp = `local-${Date.now()}-${++uploadSeq}`
      const row: Row = {
        id: temp,
        name: file.name,
        delete_token: "",
        created_at: new Date().toISOString(),
        phase: "uploading",
        progress: 0,
      }
      setRows((rs) => [row, ...rs])
      try {
        const res = await uploadFile(base, file, {
          presenter: presenter.get(),
          onProgress: (f) => patch(temp, { progress: f }),
        })
        const stored: StoredUpload = {
          id: res.drawing_id,
          name: file.name,
          delete_token: res.delete_token,
          created_at: row.created_at,
        }
        myUploads.add(stored)
        if (!alive.current) return res.drawing_id
        setRows((rs) =>
          rs.map((r) =>
            r.id === temp
              ? { ...stored, phase: "queued", progress: undefined, uid: temp }
              : r
          )
        )
        poll(res.drawing_id, stored.created_at, file.name)
        return res.drawing_id
      } catch (e) {
        setRows((rs) => rs.filter((r) => r.id !== temp))
        toast.error(tuNow("up_toast_failed", { name: file.name }), {
          description: uploadErrorText(e),
        })
        return null
      }
    },
    [base, patch, poll]
  )

  const remove = useCallback(
    async (r: Row) => {
      try {
        await deleteDrawing({ baseUrl: base }, r.id, r.delete_token)
      } catch (e) {
        // Already gone on the server counts as deleted.
        if (
          !(
            e instanceof ApiError &&
            (e.code === "unknown_drawing" || e.status === 404)
          )
        ) {
          toast.error(tuNow("up_toast_delete_failed", { name: r.name }), {
            description: uploadErrorText(e),
          })
          return
        }
      }
      removed.current.add(r.id)
      clearTimeout(timers.current.get(r.id))
      myUploads.remove(r.id)
      // Fade the row out (same path it came in), then drop it.
      setRows((rs) => rs.map((x) => (x.id === r.id ? { ...x, leaving: true } : x)))
      window.setTimeout(() => setRows((rs) => rs.filter((x) => x.id !== r.id)), 160)
      toast.success(tuNow("up_toast_deleted", { name: r.name }))
    },
    [base]
  )

  return { rows: rows.filter((r) => r.phase !== "expired"), start, remove }
}

const PHASE_LABEL: Record<Row["phase"], UiKey> = {
  uploading: "phase_uploading",
  queued: "phase_queued",
  processing: "phase_processing",
  ready: "phase_ready",
  failed: "phase_failed",
  expired: "phase_expired",
  too_slow: "phase_too_slow",
}

const ATTACHMENT_STATE = {
  uploading: "uploading",
  queued: "processing",
  processing: "processing",
  ready: "done",
  failed: "error",
  expired: "idle",
  too_slow: "error",
} as const

const PhaseIcon = ({ phase }: { phase: Row["phase"] }) =>
  phase === "ready" ? (
    <FileCheckIcon />
  ) : phase === "failed" || phase === "too_slow" ? (
    <FileWarningIcon />
  ) : phase === "uploading" || phase === "queued" || phase === "processing" ? (
    <Spinner />
  ) : (
    <FileIcon />
  )

function UiText({ k }: { k: UiKey }) {
  return <>{useUi().tu(k)}</>
}

function UploadRow({
  r,
  onDelete,
  onOpen,
}: {
  r: Row
  onDelete: (r: Row) => void
  onOpen?: (uploadId: string) => void
}) {
  const { tu } = useUi()
  const pct =
    r.progress !== undefined
      ? Math.round(Math.min(1, Math.max(0, r.progress)) * 100)
      : null
  const busy =
    r.phase === "uploading" || r.phase === "queued" || r.phase === "processing"
  return (
    <Attachment state={ATTACHMENT_STATE[r.phase]} className="w-full">
      <AttachmentMedia>
        <PhaseIcon phase={r.phase} />
      </AttachmentMedia>
      <AttachmentContent>
        <AttachmentTitle title={r.name}>{r.name}</AttachmentTitle>
        <AttachmentDescription aria-live="polite" className="whitespace-normal">
          {tu(PHASE_LABEL[r.phase])}
          {r.phase === "failed" && r.error
            ? `: ${r.error.replace(/^(DXF |The file |This file )?could not be read:?\s*/i, "")}`
            : ""}
          {r.phase === "too_slow"
            ? `: ${tu("up_too_slow", { min: UPLOAD_DEADLINE_MS / 60_000 })}`
            : ""}
          {busy && pct !== null ? (
            <span className="num"> · {pct} %</span>
          ) : null}
        </AttachmentDescription>
        {busy ? (
          <Progress
            value={pct ?? 0}
            aria-label={`${r.name}: ${tu(PHASE_LABEL[r.phase]).toLowerCase()}`}
            className={cn("mt-1.5", pct === null && "opacity-50")}
          />
        ) : null}
      </AttachmentContent>
      <AttachmentActions className="gap-1">
        {r.phase === "ready" ? (
          <Button variant="outline" asChild>
            <Link to={`/d/${encodeURIComponent(r.id)}`} onClick={() => onOpen?.(r.id)}>
              {tu("open")}
            </Link>
          </Button>
        ) : null}
        {r.delete_token ? (
          <AlertDialog>
            <Tooltip>
              <TooltipTrigger asChild>
                <AlertDialogTrigger asChild>
                  <AttachmentAction size="icon" aria-label={tu("up_delete_aria", { name: r.name })}>
                    <Trash2Icon />
                  </AttachmentAction>
                </AlertDialogTrigger>
              </TooltipTrigger>
              <TooltipContent>{tu("up_delete_tip")}</TooltipContent>
            </Tooltip>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{tu("up_delete_title", { name: r.name })}</AlertDialogTitle>
                <AlertDialogDescription>{tu("up_delete_desc")}</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>{tu("up_keep")}</AlertDialogCancel>
                <AlertDialogAction
                  variant="destructive"
                  onClick={() => onDelete(r)}
                >
                  {tu("up_delete")}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        ) : null}
      </AttachmentActions>
    </Attachment>
  )
}

const MB = 1024 * 1024

function UsageNote() {
  const { tu } = useUi()
  return (
    <p className="text-xs text-muted-foreground">
      {tu("up_usage")}{" "}
      <Link
        to="/privacy"
        className="font-medium text-foreground underline underline-offset-2"
      >
        {tu("nav_privacy")}
      </Link>
    </p>
  )
}

/** Upload card. `base` is the API base; without one the panel explains why uploads are off. */
export function UploadPanel({
  base,
  onReadyUploadsChange,
  onBundleUploads,
  onProjectsDiscovered,
  onOpenUpload,
}: UploadPanelProps) {
  const { tu } = useUi()
  return (
    <Card size="sm" role="region" aria-labelledby="upload-title">
      <CardHeader>
        <CardTitle>
          <h2 id="upload-title">
            <UiText k="upload_title" />
          </h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {base ? (
          <TooltipProvider delayDuration={400}>
            <Uploader
              base={base}
              onReadyUploadsChange={onReadyUploadsChange}
              onBundleUploads={onBundleUploads}
              onProjectsDiscovered={onProjectsDiscovered}
              onOpenUpload={onOpenUpload}
            />
          </TooltipProvider>
        ) : (
          <>
            <Alert data-uploads-off>
              <CloudOffIcon />
              <AlertTitle>{tu("up_off_title")}</AlertTitle>
              <AlertDescription>{tu("up_off_desc")}</AlertDescription>
            </Alert>
            <UsageNote />
          </>
        )}
      </CardContent>
    </Card>
  )
}

function Uploader({
  base,
  onReadyUploadsChange,
  onBundleUploads,
  onProjectsDiscovered,
  onOpenUpload,
}: {
  base: string
  onReadyUploadsChange?: (uploads: ReadyUploadSummary[]) => void
  onBundleUploads?: (uploadIds: string[]) => void
  onProjectsDiscovered?: () => void
  onOpenUpload?: (uploadId: string) => void
}) {
  const { tu, tn } = useUi()
  const { rows, start, remove } = useUploads(base, onProjectsDiscovered)
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  /** One line per file that was not uploaded (file name, reason). */
  const [rejection, setRejection] = useState<{ name: string; reason: string; n?: number }[]>([])
  /** Quick-toggle offered when 2+ files are dropped/picked in one batch. */
  const [batchIds, setBatchIds] = useState<string[]>([])
  const [bundleWhenReady, setBundleWhenReady] = useState(false)
  const autoBundledBatchRef = useRef("")

  const hasPresenter = useSyncExternalStore(
    presenter.subscribe,
    () => !!presenter.get()
  )

  const readyUploads = useMemo(
    () =>
      rows
        .filter((r) => r.phase === "ready" && !r.leaving)
        .map((r) => ({ id: r.id, name: r.name })),
    [rows]
  )

  const readySignature = readyUploads.map((u) => `${u.id}:${u.name}`).join("|")
  useEffect(() => {
    onReadyUploadsChange?.(readyUploads)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readySignature, onReadyUploadsChange])

  // Auto-open the project bundler once all files in a 2+ batch finish processing when toggled on.
  useEffect(() => {
    if (!bundleWhenReady || batchIds.length < 2 || !onBundleUploads) return
    const batchKey = batchIds.join(",")
    if (autoBundledBatchRef.current === batchKey) return
    const batchRows = rows.filter((r) => batchIds.includes(r.id))
    const anyPending = batchRows.some(
      (r) => r.phase === "uploading" || r.phase === "queued" || r.phase === "processing"
    )
    if (anyPending) return
    const readyBatchIds = batchRows.filter((r) => r.phase === "ready").map((r) => r.id)
    if (readyBatchIds.length >= 2) {
      autoBundledBatchRef.current = batchKey
      onBundleUploads(readyBatchIds)
    }
  }, [bundleWhenReady, batchIds, rows, onBundleUploads])

  const take = (files: FileList | null) => {
    const all = Array.from(files ?? [])
    if (!all.length) return
    const out: { name: string; reason: string; n?: number }[] = []
    const ok: File[] = []
    for (const file of all.slice(0, UPLOAD_BATCH)) {
      const check = checkFile(file, hasPresenter)
      if (check.ok) ok.push(file)
      else out.push({ name: file.name, reason: check.message })
    }
    const extra = all.slice(UPLOAD_BATCH)
    if (extra.length)
      out.push({
        name: extra.map((f) => f.name).join(", "),
        n: extra.length,
        reason: tn("up_batch", extra.length, { max: UPLOAD_BATCH }),
      })
    setRejection(out)
    if (ok.length < 2) {
      setBatchIds([])
      setBundleWhenReady(false)
    }
    // One after another, so the server's per-IP limits see a steady trickle, not a burst.
    void (async () => {
      const uploadedIds: string[] = []
      for (const f of ok) {
        const id = await start(f)
        if (id) uploadedIds.push(id)
      }
      if (uploadedIds.length >= 2) {
        setBatchIds(uploadedIds)
      }
    })()
  }

  const batchStillProcessing =
    batchIds.length >= 2 &&
    rows.some(
      (r) =>
        batchIds.includes(r.id) &&
        (r.phase === "uploading" || r.phase === "queued" || r.phase === "processing")
    )

  return (
    <>
      <div
        data-dropzone
        data-over={over || undefined}
        onDragOver={(e) => {
          e.preventDefault()
          e.dataTransfer.dropEffect = "copy"
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          take(e.dataTransfer.files)
        }}
        className={cn(
          "flex flex-col items-center gap-3 rounded-lg border border-dashed border-input px-4 py-6 text-center transition-colors",
          over && "border-foreground bg-muted"
        )}
      >
        <FileUpIcon className="size-6 text-muted-foreground" aria-hidden />
        <div className="flex flex-col gap-1">
          <p className="text-sm text-foreground">{tu("drop_here")}</p>
          <p className="text-xs text-muted-foreground">
            <span className="num">
              {tu("up_caps_line", {
                dxf: UPLOAD_CAPS.dxf.bytes / MB,
                ifc: UPLOAD_CAPS.ifc.bytes / MB,
                img: UPLOAD_CAPS.image.bytes / MB,
              })}
            </span>{" "}
            · {tu("for_everyone", { n: UPLOAD_BATCH })}
          </p>
        </div>
        <Button variant="outline" onClick={() => input.current?.click()}>
          {tu("choose_file")}
        </Button>
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPT}
          className="sr-only"
          tabIndex={-1}
          aria-label={tu("up_file_aria")}
          data-upload-input
          onChange={(e) => {
            take(e.currentTarget.files)
            e.currentTarget.value = ""
          }}
        />
      </div>

      <div
        className="flex items-center gap-2 text-xs text-muted-foreground"
        data-presenter={hasPresenter ? "on" : "off"}
      >
        <KeyRoundIcon className="size-3.5 shrink-0" aria-hidden />
        {hasPresenter ? (
          <>
            <p className="flex-1">{tu("up_presenter_on", { image: UPLOAD_CAPS.image.bytes / MB })}</p>
            <Button
              variant="ghost"
              onClick={() => {
                presenter.clear()
              }}
            >
              {tu("up_turn_off")}
            </Button>
          </>
        ) : (
          <p className="flex-1">{tu("up_raster")}</p>
        )}
      </div>

      {rejection.length ? (
        <Alert variant="destructive" data-upload-rejected>
          <TriangleAlertIcon />
          <AlertTitle>{tn("up_rejected", rejectedCount(rejection))}</AlertTitle>
          <AlertDescription className="min-w-0 [overflow-wrap:anywhere]">
            <ul className="flex flex-col gap-1">
              {rejection.map((r, i) => (
                <li key={i} className="line-clamp-3" title={`${r.name}: ${r.reason}`}>
                  {r.n ? (
                    // Several files: the reason first, so a long list of names can't hide it.
                    <>
                      {r.reason} <b className="font-medium break-all">{r.name}</b>
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

      <UsageNote />

      {rows.length ? (
        <div className="flex flex-col gap-2">
          <Separator />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-xs font-medium text-muted-foreground">
              {tu("your_uploads")}
            </h3>
            {onBundleUploads && batchStillProcessing ? (
              <Button
                type="button"
                variant={bundleWhenReady ? "default" : "outline"}
                aria-pressed={bundleWhenReady}
                onClick={() => setBundleWhenReady((v) => !v)}
                className="min-h-10 text-xs"
                data-bundle-uploads-toggle
              >
                <LayersIcon data-icon="inline-start" />
                {tu("proj_bundle_uploads")}
              </Button>
            ) : onBundleUploads && readyUploads.length >= 2 ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => onBundleUploads(readyUploads.map((u) => u.id))}
                className="min-h-10 text-xs"
                data-bundle-uploads
              >
                <LayersIcon data-icon="inline-start" />
                {tu("proj_bundle_uploads")}
              </Button>
            ) : null}
          </div>
          <ul className="flex flex-col gap-2" aria-label={tu("your_uploads")}>
            {rows.map((r) => (
              <li key={r.uid ?? r.id} data-upload={r.phase} className={r.leaving ? "exit-fall" : "enter-rise"}>
                <UploadRow r={r} onDelete={(x) => void remove(x)} onOpen={onOpenUpload} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  )
}

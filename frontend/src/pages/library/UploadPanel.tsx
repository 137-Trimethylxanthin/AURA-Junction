import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'
import { CloudOffIcon, FileUpIcon, KeyRoundIcon, Trash2Icon, TriangleAlertIcon } from 'lucide-react'
import { ApiError, deleteDrawing, getDrawingStatus } from '@/api/client'
import type { DrawingStatus } from '@/api/contract'
import {
  ACCEPT,
  UPLOAD_CAPS,
  checkFile,
  myUploads,
  presenter,
  uploadErrorText,
  uploadFile,
  type StoredUpload,
} from '@/api/library'
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
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/lib/utils'

/** How often an upload that is still being processed is polled. */
export const UPLOAD_POLL_MS = 1500

type Row = StoredUpload & {
  phase: 'uploading' | DrawingStatus | 'expired'
  /** 0–1: upload bytes while uploading, server progress while processing. */
  progress?: number
  error?: string
}

/** The user's own uploads: stored ids + delete tokens, with live status from the API. */
function useUploads(base: string) {
  const [rows, setRows] = useState<Row[]>(() => myUploads.list().map((u) => ({ ...u, phase: 'queued' as const })))
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const alive = useRef(true)

  const patch = useCallback((id: string, p: Partial<Row>) => {
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...p } : r)))
  }, [])

  const poll = useCallback(
    (id: string) => {
      const tick = async () => {
        clearTimeout(timers.current.get(id))
        try {
          const st = await getDrawingStatus({ baseUrl: base }, id)
          if (!alive.current) return
          patch(id, { phase: st.status, progress: st.progress, error: st.error })
          if (st.status === 'queued' || st.status === 'processing')
            timers.current.set(id, setTimeout(() => void tick(), UPLOAD_POLL_MS))
        } catch (e) {
          if (!alive.current) return
          if (e instanceof ApiError && (e.code === 'unknown_drawing' || e.status === 404)) {
            // Expired (24 h) or deleted elsewhere: forget it.
            myUploads.remove(id)
            patch(id, { phase: 'expired' })
            return
          }
          patch(id, { error: uploadErrorText(e) })
          timers.current.set(id, setTimeout(() => void tick(), UPLOAD_POLL_MS * 4))
        }
      }
      void tick()
    },
    [base, patch],
  )

  useEffect(() => {
    alive.current = true
    const map = timers.current
    for (const u of myUploads.list()) poll(u.id)
    return () => {
      alive.current = false
      for (const t of map.values()) clearTimeout(t)
      map.clear()
    }
  }, [poll])

  const start = useCallback(
    async (file: File) => {
      const temp = `local-${Date.now()}`
      const row: Row = { id: temp, name: file.name, delete_token: '', created_at: new Date().toISOString(), phase: 'uploading', progress: 0 }
      setRows((rs) => [row, ...rs])
      try {
        const res = await uploadFile(base, file, {
          presenter: presenter.get(),
          onProgress: (f) => patch(temp, { progress: f }),
        })
        const stored: StoredUpload = { id: res.drawing_id, name: file.name, delete_token: res.delete_token, created_at: row.created_at }
        myUploads.add(stored)
        if (!alive.current) return
        setRows((rs) => rs.map((r) => (r.id === temp ? { ...stored, phase: 'queued', progress: undefined } : r)))
        poll(res.drawing_id)
      } catch (e) {
        setRows((rs) => rs.filter((r) => r.id !== temp))
        toast.error(`${file.name} was not uploaded`, { description: uploadErrorText(e) })
      }
    },
    [base, patch, poll],
  )

  const remove = useCallback(
    async (r: Row) => {
      try {
        await deleteDrawing({ baseUrl: base }, r.id, r.delete_token)
      } catch (e) {
        // Already gone on the server counts as deleted.
        if (!(e instanceof ApiError && (e.code === 'unknown_drawing' || e.status === 404))) {
          toast.error(`${r.name} was not deleted`, { description: uploadErrorText(e) })
          return
        }
      }
      clearTimeout(timers.current.get(r.id))
      myUploads.remove(r.id)
      setRows((rs) => rs.filter((x) => x.id !== r.id))
      toast.success(`${r.name} deleted from the server`)
    },
    [base],
  )

  return { rows: rows.filter((r) => r.phase !== 'expired'), start, remove }
}

const PHASE_LABEL: Record<Row['phase'], string> = {
  uploading: 'Uploading',
  queued: 'Queued',
  processing: 'Processing',
  ready: 'Ready',
  failed: 'Could not be read',
  expired: 'Expired',
}

function UploadRow({ r, onDelete }: { r: Row; onDelete: (r: Row) => void }) {
  const pct = r.progress !== undefined ? Math.round(Math.min(1, Math.max(0, r.progress)) * 100) : null
  const busy = r.phase === 'uploading' || r.phase === 'queued' || r.phase === 'processing'
  return (
    <li className="flex items-center gap-3 py-2" data-upload={r.phase}>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-foreground" title={r.name}>
          {r.name}
        </p>
        <div className="flex items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
          <span className={cn(r.phase === 'failed' && 'text-destructive')}>
            {PHASE_LABEL[r.phase]}
            {r.phase === 'failed' && r.error ? `: ${r.error}` : ''}
            {busy && pct !== null ? <span className="num"> · {pct} %</span> : null}
          </span>
        </div>
        {busy ? (
          <Progress
            value={pct ?? 0}
            aria-label={`${r.name}: ${PHASE_LABEL[r.phase].toLowerCase()}`}
            className={cn('mt-1.5', pct === null && 'opacity-50')}
          />
        ) : null}
      </div>
      {r.phase === 'ready' ? (
        <Button variant="outline" asChild>
          <Link to={`/d/${encodeURIComponent(r.id)}`}>Open</Link>
        </Button>
      ) : null}
      {r.delete_token ? (
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button variant="ghost" size="icon" aria-label={`Delete ${r.name}`} title="Delete from the server">
              <Trash2Icon />
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete {r.name}?</AlertDialogTitle>
              <AlertDialogDescription>
                The file and everything computed from it are removed from the server now, instead of after 24 hours.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Keep</AlertDialogCancel>
              <AlertDialogAction variant="destructive" onClick={() => onDelete(r)}>
                Delete
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      ) : null}
    </li>
  )
}

const MB = 1024 * 1024

function UsageNote() {
  return (
    <p className="text-xs text-muted-foreground">
      Upload only drawings you may share. Uploads are processed on our server and deleted after 24 hours; drawings
      without CAD data are sent to Google Gemini for detection and checks.{' '}
      <Link to="/privacy" className="font-medium text-foreground underline underline-offset-2">
        Privacy
      </Link>
    </p>
  )
}

/** Upload card. `base` is the API base; without one the panel explains why uploads are off. */
export function UploadPanel({ base }: { base: string | null }) {
  return (
    <section aria-labelledby="upload-title" className="flex flex-col gap-3 rounded-xl bg-card p-4 ring-1 ring-foreground/10">
      <h2 id="upload-title" className="text-sm font-medium">
        Upload a drawing
      </h2>
      {base ? (
        <Uploader base={base} />
      ) : (
        <>
          <Alert data-uploads-off>
            <CloudOffIcon />
            <AlertTitle>Uploads need the AURA server</AlertTitle>
            <AlertDescription>
              This build runs without a server, so only the bundled sample drawing is available. Its answers are
              computed in your browser.
            </AlertDescription>
          </Alert>
          <UsageNote />
        </>
      )}
    </section>
  )
}

function Uploader({ base }: { base: string }) {
  const { rows, start, remove } = useUploads(base)
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const [rejection, setRejection] = useState<string | null>(null)
  const hasPresenter = useSyncExternalStore(presenter.subscribe, () => !!presenter.get())

  const take = (files: FileList | null) => {
    const file = files?.[0]
    if (!file) return
    const check = checkFile(file, hasPresenter)
    if (!check.ok) {
      setRejection(`${file.name}: ${check.message}`)
      return
    }
    setRejection(null)
    void start(file)
  }

  return (
    <>
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
          take(e.dataTransfer.files)
        }}
        className={cn(
          'flex flex-col items-center gap-3 rounded-lg border border-dashed border-input/60 px-4 py-6 text-center transition-colors',
          over && 'border-foreground bg-muted',
        )}
      >
        <FileUpIcon className="size-6 text-muted-foreground" aria-hidden />
        <div className="flex flex-col gap-1">
          <p className="text-sm text-foreground">Drop a DXF file here</p>
          <p className="text-xs text-muted-foreground">
            <span className="num">DXF ≤ {UPLOAD_CAPS.dxf.bytes / MB} MB</span> for everyone
          </p>
        </div>
        <Button variant="outline" onClick={() => input.current?.click()}>
          Choose a file
        </Button>
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          className="sr-only"
          tabIndex={-1}
          aria-label="Drawing file"
          data-upload-input
          onChange={(e) => {
            take(e.currentTarget.files)
            e.currentTarget.value = ''
          }}
        />
      </div>

      <div className="flex items-center gap-2 text-xs text-muted-foreground" data-presenter={hasPresenter ? 'on' : 'off'}>
        <KeyRoundIcon className="size-3.5 shrink-0" aria-hidden />
        {hasPresenter ? (
          <>
            <p className="flex-1">
              Presenter access is on: PNG and JPG <span className="num">≤ {UPLOAD_CAPS.image.bytes / MB} MB</span> and
              PDF <span className="num">≤ {UPLOAD_CAPS.pdf.bytes / MB} MB</span> can be uploaded.
            </p>
            <Button
              variant="ghost"
              onClick={() => {
                presenter.clear()
              }}
            >
              Turn off
            </Button>
          </>
        ) : (
          <p className="flex-1">PNG, JPG and PDF drawings need presenter access, because they are sent to Google Gemini.</p>
        )}
      </div>

      {rejection ? (
        <Alert variant="destructive" data-upload-rejected>
          <TriangleAlertIcon />
          <AlertTitle>Not uploaded</AlertTitle>
          <AlertDescription>{rejection}</AlertDescription>
        </Alert>
      ) : null}

      <UsageNote />

      {rows.length ? (
        <div className="flex flex-col gap-1 border-t pt-3">
          <h3 className="text-xs font-medium text-muted-foreground">Your uploads</h3>
          <ul className="flex flex-col divide-y" aria-label="Your uploads">
            {rows.map((r) => (
              <UploadRow key={r.id} r={r} onDelete={(x) => void remove(x)} />
            ))}
          </ul>
        </div>
      ) : null}
    </>
  )
}

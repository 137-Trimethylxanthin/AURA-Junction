import { tu as tuNow } from '@/i18n/uiLang'
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { toast } from 'sonner'
import { ApiError, deleteDrawing, getAcceptedUploads, getDrawingStatus, getHealth, listProjects, reindexDrawing } from '@/api/client'
import type { AcceptedType, DrawingStatusBody } from '@/api/contract'
import {
  UPLOAD_CAPS,
  myProjects,
  myUploads,
  presenter,
  uploadErrorText,
  uploadFile,
  type StoredUpload,
} from '@/api/library'
import { STORE_STEPS, TOO_SLOW_MS, isLive, stageView, type Phase, type Step } from './stages'

/** How often a file still being processed is polled; store stages are slower, so they are polled less. */
export const POLL_MS = 1500
const POLL_STORE_MS = 4000
/** A backend without stages: queued/processing this long after upload is reported as stuck (as before). */
const LEGACY_DEADLINE_MS = 3 * 60_000
/** Files taken from one drop or pick; the rest are listed as not uploaded. */
export const UPLOAD_BATCH = 5

export interface UploadRow extends StoredUpload {
  /** Stable React key: the temporary id while uploading stays the key after the server id arrives. */
  uid?: string
  phase: Phase
  /** 0-1 bytes sent while uploading (measured by the browser). */
  sent?: number
  /** Latest status body from the server. */
  status?: DrawingStatusBody
  /** Last pipeline step seen, so a failure is drawn where it happened. */
  last?: Step
  /** First time a store stage was seen (ms). */
  storeSince?: number
  /** Gave up waiting (too slow). */
  slow?: boolean
  /** Transient transport problem while polling (shown small, polling continues). */
  pollError?: string
  /** Being removed: plays the exit fade before it leaves the list. */
  leaving?: boolean
  /** Bytes, when the file was picked in this session. */
  size?: number
  /** Refused before a drawing existed (the server's own words when it gave any). */
  localError?: string
}

export interface Accepted {
  types: AcceptedType[]
  ttlHours: number
  /** False: the built-in list (an older backend without GET /uploads/accepted). */
  fromServer: boolean
}

const MB = 1024 * 1024
/** Built-in list, used until (or unless) the server says what it takes. */
export const BUILTIN_ACCEPTED: Accepted = {
  types: [
    { type: 'DXF', ext: UPLOAD_CAPS.dxf.exts, max_mb: UPLOAD_CAPS.dxf.bytes / MB },
    { type: 'IFC', ext: UPLOAD_CAPS.ifc.exts, max_mb: UPLOAD_CAPS.ifc.bytes / MB },
    { type: 'PDF', ext: UPLOAD_CAPS.pdf.exts, max_mb: UPLOAD_CAPS.pdf.bytes / MB },
    { type: 'PNG, JPG or WebP', ext: UPLOAD_CAPS.image.exts, max_mb: UPLOAD_CAPS.image.bytes / MB, presenter_only: true },
  ],
  ttlHours: 24,
  fromServer: false,
}

/** What the server accepts in its current mode; the built-in list while loading or on an older backend. */
export function useAccepted(base: string | null): Accepted {
  const [acc, setAcc] = useState<Accepted>(BUILTIN_ACCEPTED)
  useEffect(() => {
    if (!base) return
    let alive = true
    getAcceptedUploads({ baseUrl: base })
      .then((a) => {
        if (alive && a && a.types.length) setAcc({ types: a.types, ttlHours: a.ttl_hours ?? 24, fromServer: true })
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [base])
  return acc
}

export const acceptAttr = (a: Accepted) => [...new Set(a.types.flatMap((t) => t.ext))].join(',')

/** Client-side check against the accepted list (the server still decides from the file's bytes). */
export function checkAgainst(
  file: { name: string; size: number },
  a: Accepted,
  hasPresenter: boolean,
): { ok: true } | { ok: false; message: string } {
  const name = file.name.toLowerCase()
  const t = a.types.find((x) => x.ext.some((e) => name.endsWith(e.toLowerCase())))
  if (!t) return { ok: false, message: tuNow('ue_type') }
  if (file.size === 0) return { ok: false, message: tuNow('ue_empty') }
  if (file.size > t.max_mb * MB) return { ok: false, message: tuNow('ue_big', { type: t.type, mb: t.max_mb }) }
  if (t.presenter_only && !hasPresenter) return { ok: false, message: tuNow('ue_presenter') }
  return { ok: true }
}

/** The server's refusal in its own words when it sent any, else our wording for the error code. */
const refusalText = (e: unknown) => (e instanceof ApiError && e.message && e.status < 500 ? e.message : uploadErrorText(e))

export interface Rejection {
  name: string
  reason: string
  n?: number
}

let seq = 0

/** Server-made multi-page PDF projects become visible to the library once a PDF finishes. */
export async function discoverServerProjects(base: string, onDone?: () => void) {
  try {
    const projects = await listProjects({ baseUrl: base }, { headers: myProjects.listHeaders() })
    if (projects.length > 0) myProjects.syncFromServer(projects)
  } catch {
    /* network / 404 / storage: nothing to sync */
  } finally {
    onDone?.()
  }
}

/** Files dropped elsewhere (the library card) and handed to the upload page on navigation. */
export const uploadHandoff = {
  files: [] as File[],
  take(): File[] {
    const f = this.files
    this.files = []
    return f
  },
}

/** Full or Quick mode from /health (`store` on/off); null while unknown or on an older backend. */
export function useStoreMode(base: string | null): 'full' | 'quick' | null {
  const [mode, setMode] = useState<'full' | 'quick' | null>(null)
  useEffect(() => {
    if (!base) return
    let alive = true
    getHealth({ baseUrl: base })
      .then((h) => {
        if (!alive || h.store === undefined) return
        setMode(h.store === 'on' || h.store === true ? 'full' : 'quick')
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [base])
  return mode
}

export const useHasPresenter = () => useSyncExternalStore(presenter.subscribe, () => !!presenter.get())

/** The user's own uploads: stored ids and delete tokens, with live status from the API. */
export function useUploads(base: string, opts: { onProjectsDiscovered?: () => void; mode?: 'full' | 'quick' | null } = {}) {
  const [rows, setRows] = useState<UploadRow[]>(() => myUploads.list().map((u) => ({ ...u, phase: 'queued' as const })))
  const [rejected, setRejected] = useState<Rejection[]>([])
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const alive = useRef(true)
  const removed = useRef(new Set<string>())
  const rowsRef = useRef(rows)
  const modeRef = useRef(opts.mode ?? undefined)
  const onDiscovered = useRef(opts.onProjectsDiscovered)
  useEffect(() => {
    rowsRef.current = rows
    modeRef.current = opts.mode ?? undefined
    onDiscovered.current = opts.onProjectsDiscovered
  })

  const patch = useCallback((id: string, p: Partial<UploadRow> | ((r: UploadRow) => Partial<UploadRow>)) => {
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...(typeof p === 'function' ? p(r) : p) } : r)))
  }, [])

  const poll = useCallback(
    (id: string) => {
      const started = Date.now()
      let discovered = false
      const tick = async () => {
        clearTimeout(timers.current.get(id))
        const row = rowsRef.current.find((r) => r.id === id)
        try {
          const st = await getDrawingStatus({ baseUrl: base }, id)
          if (!alive.current || removed.current.has(id)) return
          const now = Date.now()
          const mode = st.mode ?? modeRef.current
          const prev = row?.last
          const view0 = stageView(st.status, st.stage, { mode, last: prev })
          const inStore = STORE_STEPS.has(view0.step) && view0.outcome === 'working'
          const storeSince = inStore ? (row?.storeSince ?? now - (st.stage_s ?? 0) * 1000) : row?.storeSince
          const sent = row ? Date.parse(row.created_at) : NaN
          const legacyStuck =
            !st.stage && (st.status === 'queued' || st.status === 'processing') && Number.isFinite(sent) && now - sent > LEGACY_DEADLINE_MS
          const slow = legacyStuck || (inStore && storeSince !== undefined && now - storeSince > TOO_SLOW_MS)
          const view = stageView(st.status, st.stage, { mode, last: prev, slow })
          patch(id, {
            phase: st.status,
            status: st,
            last: view.outcome === 'working' || view.outcome === 'done' || view.outcome === 'quick' ? view.step : prev,
            storeSince,
            slow: slow || undefined,
            pollError: undefined,
          })
          if (legacyStuck) patch(id, { last: 'parsing' })
          if (st.status === 'ready' && !discovered && (row?.name ?? '').toLowerCase().endsWith('.pdf')) {
            discovered = true
            void discoverServerProjects(base, onDiscovered.current)
          }
          if (isLive(view) && !slow) timers.current.set(id, setTimeout(() => void tick(), inStore ? POLL_STORE_MS : POLL_MS))
        } catch (e) {
          if (!alive.current || removed.current.has(id)) return
          if (e instanceof ApiError && (e.code === 'unknown_drawing' || e.status === 404)) {
            myUploads.remove(id) // expired (24 h) or deleted elsewhere
            patch(id, { phase: 'expired' })
            return
          }
          if (Date.now() - started > LEGACY_DEADLINE_MS) {
            patch(id, { slow: true, pollError: undefined })
            return
          }
          patch(id, { pollError: uploadErrorText(e) })
          timers.current.set(id, setTimeout(() => void tick(), POLL_MS * 4))
        }
      }
      void tick()
    },
    [base, patch],
  )

  useEffect(() => {
    alive.current = true
    const map = timers.current
    // No API base: nothing to ask (a 404 from the wrong origin would wrongly forget the uploads).
    if (base) for (const u of myUploads.list()) poll(u.id)
    return () => {
      alive.current = false
      for (const t of map.values()) clearTimeout(t)
      map.clear()
    }
  }, [poll, base])

  const startOne = useCallback(
    async (file: File): Promise<string | null> => {
      const temp = `local-${Date.now()}-${++seq}`
      const row: UploadRow = {
        id: temp,
        name: file.name,
        delete_token: '',
        created_at: new Date().toISOString(),
        phase: 'uploading',
        sent: 0,
        size: file.size,
      }
      setRows((rs) => [row, ...rs])
      try {
        const res = await uploadFile(base, file, { presenter: presenter.get(), onProgress: (f) => patch(temp, { sent: f }) })
        const stored: StoredUpload = { id: res.drawing_id, name: file.name, delete_token: res.delete_token, created_at: row.created_at }
        myUploads.add(stored)
        if (!alive.current) return res.drawing_id
        setRows((rs) =>
          rs.map((r) => (r.id === temp ? { ...stored, phase: 'queued', uid: temp, size: file.size, last: 'queued' } : r)),
        )
        rowsRef.current = rowsRef.current.map((r) => (r.id === temp ? { ...r, ...stored, phase: 'queued' } : r))
        poll(res.drawing_id)
        return res.drawing_id
      } catch (e) {
        // Keep a failed row with the server's message (an SVG without the full setup, a DWG without the converter, ...).
        patch(temp, { phase: 'failed', sent: undefined, last: 'upload', localError: refusalText(e) })
        return null
      }
    },
    [base, patch, poll],
  )

  /** Checks a drop or pick against the caps, then uploads the accepted files one after another. */
  const add = useCallback(
    async (files: FileList | File[] | null, hasPresenter: boolean, accepted: Accepted = BUILTIN_ACCEPTED): Promise<string[]> => {
      const all = Array.from(files ?? [])
      if (!all.length) return []
      const out: Rejection[] = []
      const ok: File[] = []
      for (const file of all.slice(0, UPLOAD_BATCH)) {
        const check = checkAgainst(file, accepted, hasPresenter)
        if (check.ok) ok.push(file)
        else out.push({ name: file.name, reason: check.message })
      }
      const extra = all.slice(UPLOAD_BATCH)
      if (extra.length) out.push({ name: extra.map((f) => f.name).join(', '), n: extra.length, reason: '' })
      setRejected(out)
      // One after another, so the server's per-IP limits see a steady trickle, not a burst.
      const ids: string[] = []
      for (const f of ok) {
        const id = await startOne(f)
        if (id) ids.push(id)
      }
      return ids
    },
    [startOne],
  )

  const remove = useCallback(
    async (r: UploadRow) => {
      if (r.id.startsWith('local-')) {
        setRows((rs) => rs.filter((x) => x.id !== r.id)) // refused before upload: nothing on the server
        return
      }
      try {
        await deleteDrawing({ baseUrl: base }, r.id, r.delete_token)
      } catch (e) {
        if (!(e instanceof ApiError && (e.code === 'unknown_drawing' || e.status === 404))) {
          toast.error(tuNow('up_toast_delete_failed', { name: r.name }), { description: uploadErrorText(e) })
          return
        }
      }
      removed.current.add(r.id)
      clearTimeout(timers.current.get(r.id))
      myUploads.remove(r.id)
      setRows((rs) => rs.map((x) => (x.id === r.id ? { ...x, leaving: true } : x)))
      window.setTimeout(() => setRows((rs) => rs.filter((x) => x.id !== r.id)), 160)
      toast.success(tuNow('up_toast_deleted', { name: r.name }))
    },
    [base],
  )

  /**
   * Retry. A failed or partial store run with a delete token: POST /drawings/:id/reindex (every page), then poll.
   * Otherwise (too slow, no token): clear the wait and poll afresh.
   */
  const retry = useCallback(
    async (r: UploadRow, reindex = false) => {
      if (reindex && r.delete_token) {
        try {
          const st = await reindexDrawing({ baseUrl: base }, r.id, r.delete_token)
          if (st) patch(r.id, { status: st, phase: st.status })
        } catch (e) {
          const quick = e instanceof ApiError && e.status === 409
          toast.error(tuNow(quick ? 'ul_retry_quick' : 'ul_retry_failed'), quick ? undefined : { description: refusalText(e) })
          if (quick) return
        }
      }
      patch(r.id, { slow: undefined, storeSince: undefined, pollError: undefined })
      rowsRef.current = rowsRef.current.map((x) => (x.id === r.id ? { ...x, slow: undefined, storeSince: undefined, last: 'reading' } : x))
      poll(r.id)
    },
    [base, patch, poll],
  )

  /** Track an upload this browser has no token for (a shared /upload/:id link): status only, no delete. */
  const watch = useCallback(
    (id: string) => {
      if (rowsRef.current.some((r) => r.id === id)) return
      const row: UploadRow = { id, name: id, delete_token: '', created_at: new Date().toISOString(), phase: 'queued' }
      rowsRef.current = [row, ...rowsRef.current]
      setRows((rs) => (rs.some((r) => r.id === id) ? rs : [row, ...rs]))
      poll(id)
    },
    [poll],
  )

  // One array per change of `rows`: a fresh filter on every render made everything derived from it new each time.
  const visible = useMemo(() => rows.filter((r) => r.phase !== 'expired'), [rows])
  return { rows: visible, rejected, watch, clearRejected: () => setRejected([]), add, remove, retry }
}

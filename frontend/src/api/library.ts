import { ApiError, TransportError } from './client'
import { tu } from '@/i18n/uiLang'
import {
  fastApiMessage,
  isErrorBody,
  isUploadResult,
  licenceLabel,
  safeUrl,
  type Credit,
  type DrawingKind,
  type DrawingSummary,
  type UploadResult,
} from './contract'

/**
 * Thin, JSON-free helpers for the library page: API base, presenter token, the user's own uploads and the upload
 * request itself. Nothing here imports the bundled drawing, so /app stays a small chunk.
 */

export function apiBase(): string | null {
  const v = (import.meta.env?.VITE_API_URL as string | undefined) ?? ''
  return v.trim() || null
}

/** Resolve a root-relative URL served by the API (thumbnails, images) against the API base. */
export function apiUrl(url: string, base = apiBase()): string | null {
  const safe = safeUrl(url)
  if (!safe || !base || !safe.startsWith('/')) return safe
  return base.replace(/\/+$/, '') + safe
}

// ---- storage (localStorage can throw in private mode; every access is guarded) -----------------------------

function read(key: string): string | null {
  try {
    return globalThis.localStorage?.getItem(key) ?? null
  } catch {
    return null
  }
}
function write(key: string, value: string | null) {
  try {
    if (value === null) globalThis.localStorage?.removeItem(key)
    else globalThis.localStorage?.setItem(key, value)
  } catch {
    /* storage unavailable: the value lives for this page only */
  }
}

const PRESENTER_KEY = 'aura.presenter'
const UPLOADS_KEY = 'aura.uploads'

const presenterListeners = new Set<() => void>()
const notifyPresenter = () => presenterListeners.forEach((fn) => fn())

/** The presenter token arrives through a `?presenter=` link and is never part of the bundle. */
export const presenter = {
  get: (): string | null => read(PRESENTER_KEY),
  set: (token: string) => {
    write(PRESENTER_KEY, token)
    notifyPresenter()
  },
  clear: () => {
    write(PRESENTER_KEY, null)
    notifyPresenter()
  },
  /** For useSyncExternalStore: the link is captured at the root, possibly after a page has mounted. */
  subscribe: (fn: () => void) => {
    presenterListeners.add(fn)
    return () => {
      presenterListeners.delete(fn)
    }
  },
}

export interface StoredUpload {
  id: string
  /** The file name as the user picked it; shown only in this browser. */
  name: string
  delete_token: string
  created_at: string
}

export const myUploads = {
  list(): StoredUpload[] {
    try {
      const v: unknown = JSON.parse(read(UPLOADS_KEY) ?? '[]')
      return Array.isArray(v)
        ? v.filter(
            (u): u is StoredUpload =>
              !!u && typeof u.id === 'string' && typeof u.delete_token === 'string' && typeof u.name === 'string',
          )
        : []
    } catch {
      return []
    }
  },
  add(u: StoredUpload) {
    write(UPLOADS_KEY, JSON.stringify([u, ...myUploads.list().filter((x) => x.id !== u.id)]))
  },
  remove(id: string) {
    write(UPLOADS_KEY, JSON.stringify(myUploads.list().filter((x) => x.id !== id)))
  },
}

// ---- library card display helpers (pure, JSON-free) -------------------------------------------------------

export const KIND_LABEL: Record<DrawingKind, string> = {
  cad_dxf: 'CAD · DXF',
  raster: 'Image',
  ifc: 'IFC',
}

/** Drawing kind in the interface language; distinguishes PDF uploads (`source_ext === '.pdf'`) from raster images. */
export function kindText(
  k: DrawingKind,
  tuFn: (key: 'kind_image' | 'kind_pdf') => string = tu,
  sourceExt?: string,
): string {
  if (k === 'raster' && sourceExt?.toLowerCase() === '.pdf') return tuFn('kind_pdf')
  return k === 'raster' ? tuFn('kind_image') : KIND_LABEL[k]
}

const nf = new Intl.NumberFormat('en-US')

/** "38/38": only from measured facts; null when the API sends none. */
export function factsRatio(d: Pick<DrawingSummary, 'facts'>): string | null {
  const f = d.facts
  if (!f || f.gold_total <= 0) return null
  return `${nf.format(f.correct)}/${nf.format(f.gold_total)}`
}

/** Replaces generic IFC exporter placeholder `"Project Name"` with the dataset project name while preserving real titles. */
export function displayDrawingTitle(d: Pick<DrawingSummary, 'title'> & Partial<Pick<DrawingSummary, 'id' | 'credit'>>): string {
  const raw = d.title.trim()
  if (!/^project name\b/i.test(raw)) return raw
  const rest = raw.replace(/^project name\b/i, '')
  const creditHead = d.credit?.text?.split(/[:(]/)[0]?.replace(/\s+demo site\b/i, '')?.trim()
  if (creditHead && !/^project name\b/i.test(creditHead)) {
    return `${creditHead}${rest}`
  }
  if (d.id) {
    const slug = d.id
      .replace(/^ifc-/, '')
      .split(/--|_/)[0]
      .replace(/-/g, ' ')
      .trim()
    if (slug) {
      const titled = slug.replace(/\b\w/g, (c) => c.toUpperCase())
      return `${titled}${rest}`
    }
  }
  return raw
}

/** Compact 1-line attribution for library cards that always preserves the licence token (`CC BY 4.0`, `CC BY-NC 4.0`, etc.). */
export function compactCredit(credit: Credit): string {
  const lic = licenceLabel(credit)
  const text = credit.text.trim()
  if (/Swiss Dwellings/i.test(text)) {
    const m = /Swiss Dwellings(?:\s+v[\d.]+)?/i.exec(text)
    return `${lic} · ${m ? m[0] : 'Swiss Dwellings'}`
  }
  if (/BIM-Speed Warmond/i.test(text)) {
    return `${lic} · BIM-Speed Warmond`
  }
  if (/KIT IFC Examples/i.test(text)) {
    return `${lic} · IAI / KIT`
  }
  let short = text.split(/\.\s+|\s+doi:/i)[0].trim()
  if (short.toLowerCase().endsWith(`, ${lic.toLowerCase()}`)) {
    short = short.slice(0, -(lic.length + 2)).trim()
  }
  if (!short || short.toLowerCase() === lic.toLowerCase()) return lic
  if (short.toLowerCase().includes(lic.toLowerCase())) return short
  return `${lic} · ${short}`
}

// ---- client-side checks mirroring the server caps (the server decides from magic bytes) --------------------

export type UploadType = 'dxf' | 'ifc' | 'image' | 'pdf'

const MB = 1024 * 1024
export const UPLOAD_CAPS: Record<UploadType, { bytes: number; label: string; exts: string[]; presenter: boolean }> = {
  dxf: { bytes: 20 * MB, label: 'DXF', exts: ['.dxf'], presenter: false },
  ifc: { bytes: 60 * MB, label: 'IFC', exts: ['.ifc'], presenter: false },
  pdf: { bytes: 20 * MB, label: 'PDF', exts: ['.pdf'], presenter: false },
  image: { bytes: 15 * MB, label: 'PNG, JPG or WebP', exts: ['.png', '.jpg', '.jpeg', '.webp'], presenter: true },
}
export const ACCEPT = [...Object.values(UPLOAD_CAPS).flatMap((c) => c.exts), 'application/pdf'].join(',')

export type FileCheck =
  | { ok: true; type: UploadType }
  | { ok: false; code: 'unsupported_type' | 'too_large' | 'presenter_required' | 'empty'; message: string }

export function checkFile(file: { name: string; size: number }, hasPresenter: boolean): FileCheck {
  const name = file.name.toLowerCase()
  const type = (Object.keys(UPLOAD_CAPS) as UploadType[]).find((k) => UPLOAD_CAPS[k].exts.some((e) => name.endsWith(e)))
  if (!type) return { ok: false, code: 'unsupported_type', message: tu('ue_type') }
  const cap = UPLOAD_CAPS[type]
  if (file.size === 0) return { ok: false, code: 'empty', message: tu('ue_empty') }
  if (file.size > cap.bytes)
    return { ok: false, code: 'too_large', message: tu('ue_big', { type: cap.label, mb: cap.bytes / MB }) }
  if (cap.presenter && !hasPresenter)
    return {
      ok: false,
      code: 'presenter_required',
      message: tu('ue_presenter'),
    }
  return { ok: true, type }
}

/** Messages for the server's error codes, in the user's terms. */
export function uploadErrorText(e: unknown): string {
  if (e instanceof ApiError) {
    switch (e.code) {
      case 'too_large':
        return tu('ue_srv_big')
      case 'unsupported_type':
        return tu('ue_srv_type')
      case 'presenter_required':
        return tu('ue_srv_presenter')
      case 'rate_limited':
        // The server says which limit (hourly uploads, a full queue, storage): show its words.
        return e.message || tu('ue_rate')
      case 'budget_exhausted':
        return tu('ue_budget')
      default:
        return e.message
    }
  }
  if (e instanceof TransportError)
    return e.kind === 'server'
      ? tu('ue_srv_500')
      : tu('ue_net')
  return e instanceof Error ? e.message : tu('ue_failed')
}

/**
 * POST /uploads as multipart with upload progress (fetch has no upload progress, so this uses XHR).
 * Resolves with the contract's UploadResult; rejects with ApiError (4xx, invalid body) or TransportError.
 */
export function uploadFile(
  base: string,
  file: File,
  opts: { presenter?: string | null; onProgress?: (fraction: number) => void; signal?: AbortSignal } = {},
): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', base.replace(/\/+$/, '') + '/uploads')
    if (opts.presenter) xhr.setRequestHeader('X-AURA-Presenter', opts.presenter)
    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable && ev.total > 0) opts.onProgress?.(ev.loaded / ev.total)
    }
    xhr.onerror = () => reject(new TransportError('Network error', 'network'))
    xhr.onabort = () => reject(new TransportError('Upload cancelled', 'network'))
    xhr.onload = () => {
      let body: unknown
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        body = null
      }
      if (xhr.status >= 500) return reject(new TransportError(`Server error ${xhr.status}`, 'server', xhr.status))
      if (xhr.status >= 400) {
        if (isErrorBody(body)) return reject(new ApiError(body.error, xhr.status, body.code, body))
        const detail = fastApiMessage(body)
        return reject(new ApiError(detail ? `Upload not accepted: ${detail}` : `Upload failed (${xhr.status})`, xhr.status, 'bad_request', body))
      }
      if (!isUploadResult(body)) return reject(new ApiError('The upload reply does not match the contract', xhr.status, 'invalid_response', body))
      opts.onProgress?.(1)
      resolve(body)
    }
    opts.signal?.addEventListener('abort', () => xhr.abort())
    const form = new FormData()
    form.append('file', file)
    xhr.send(form)
  })
}

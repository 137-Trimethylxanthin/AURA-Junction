import { ApiError, TransportError } from './client'
import { fastApiMessage, isErrorBody, isUploadResult, type UploadResult } from './contract'

/**
 * Thin, JSON-free helpers for the library page: API base, presenter token, the user's own uploads and the upload
 * request itself. Nothing here imports the bundled drawing, so /app stays a small chunk.
 */

export function apiBase(): string | null {
  const v = (import.meta.env?.VITE_API_URL as string | undefined) ?? ''
  return v.trim() || null
}

/** Resolve a root-relative URL served by the API (thumbnails, images) against the API base. */
export function apiUrl(url: string, base = apiBase()): string {
  if (!base || !url.startsWith('/') || url.startsWith('//')) return url
  return base.replace(/\/+$/, '') + url
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

// ---- client-side checks mirroring the server caps (the server decides from magic bytes) --------------------

export type UploadType = 'dxf' | 'image' | 'pdf'

const MB = 1024 * 1024
export const UPLOAD_CAPS: Record<UploadType, { bytes: number; label: string; exts: string[]; presenter: boolean }> = {
  dxf: { bytes: 20 * MB, label: 'DXF', exts: ['.dxf'], presenter: false },
  image: { bytes: 15 * MB, label: 'PNG or JPG', exts: ['.png', '.jpg', '.jpeg'], presenter: true },
  pdf: { bytes: 20 * MB, label: 'PDF', exts: ['.pdf'], presenter: true },
}
export const ACCEPT = Object.values(UPLOAD_CAPS)
  .flatMap((c) => c.exts)
  .join(',')

export type FileCheck =
  | { ok: true; type: UploadType }
  | { ok: false; code: 'unsupported_type' | 'too_large' | 'presenter_required' | 'empty'; message: string }

export function checkFile(file: { name: string; size: number }, hasPresenter: boolean): FileCheck {
  const name = file.name.toLowerCase()
  const type = (Object.keys(UPLOAD_CAPS) as UploadType[]).find((k) => UPLOAD_CAPS[k].exts.some((e) => name.endsWith(e)))
  if (!type) return { ok: false, code: 'unsupported_type', message: 'Only DXF files can be uploaded. DWG and zip files are not accepted.' }
  const cap = UPLOAD_CAPS[type]
  if (file.size === 0) return { ok: false, code: 'empty', message: 'This file is empty.' }
  if (file.size > cap.bytes)
    return { ok: false, code: 'too_large', message: `${cap.label} files can be at most ${cap.bytes / MB} MB.` }
  if (cap.presenter && !hasPresenter)
    return {
      ok: false,
      code: 'presenter_required',
      message: 'Needs CAD data. Image-only drawings are a model estimate and are not open yet. DXF works for everyone.',
    }
  return { ok: true, type }
}

/** Messages for the server's error codes, in the user's terms. */
export function uploadErrorText(e: unknown): string {
  if (e instanceof ApiError) {
    switch (e.code) {
      case 'too_large':
        return 'The server says this file is too large.'
      case 'unsupported_type':
        return 'The server could not read this file type. DWG and zip files are not accepted.'
      case 'presenter_required':
        return 'This file type needs presenter access.'
      case 'rate_limited':
        return 'Too many uploads from here. Try again in a while.'
      case 'budget_exhausted':
        return 'Today’s model budget is used up. DXF uploads still work.'
      default:
        return e.message
    }
  }
  if (e instanceof TransportError)
    return e.kind === 'server'
      ? 'The server had a problem with this file. Try again in a moment.'
      : 'The server could not be reached. Try again in a moment.'
  return e instanceof Error ? e.message : 'The upload failed.'
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

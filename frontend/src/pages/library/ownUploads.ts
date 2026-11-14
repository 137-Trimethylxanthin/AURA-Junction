import type { DrawingKind, DrawingSummary, UploadCounts } from '@/api/contract'
import type { UploadRow } from '@/pages/upload/useUploads'
import { estimatedCounts, stageView, type StageView } from '@/pages/upload/stages'

/**
 * The library shows the user's own finished uploads as ordinary cards. Finished = the drawing opens (status
 * ready); a store stage still running shows as a small badge on the card. In-progress and failed uploads stay on
 * /upload. Uploads are known from this browser's stored ids (the server does not list them in GET /drawings).
 */

/** Whether the bundled demo drawings are listed. Off in dev (.env.development); on by default (the hosted build). */
export const LIBRARY_SHOWS_BUNDLED = (import.meta.env?.VITE_LIBRARY_BUNDLED as string | undefined) !== '0'

export type FileType = 'DXF' | 'IFC' | 'PDF' | 'PNG' | 'JPG' | 'SVG' | 'WEBP' | 'IMAGE'

const EXT: Record<string, FileType> = {
  dxf: 'DXF',
  ifc: 'IFC',
  pdf: 'PDF',
  png: 'PNG',
  jpg: 'JPG',
  jpeg: 'JPG',
  svg: 'SVG',
  webp: 'WEBP',
}

/** File type of a library entry: the upload's extension, else the drawing kind. */
export function fileTypeOf(d: Pick<DrawingSummary, 'title' | 'kind' | 'source_ext'> & { name?: string }): FileType {
  const ext = (d.source_ext ?? /\.([a-z0-9]+)$/i.exec(d.name ?? d.title)?.[0] ?? '').replace(/^\./, '').toLowerCase()
  if (EXT[ext]) return EXT[ext]
  return d.kind === 'cad_dxf' ? 'DXF' : d.kind === 'ifc' ? 'IFC' : 'IMAGE'
}

export function kindFromName(name: string): DrawingKind {
  const t = fileTypeOf({ title: name, kind: 'raster' })
  return t === 'DXF' ? 'cad_dxf' : t === 'IFC' ? 'ifc' : 'raster'
}

export interface OwnUpload {
  summary: DrawingSummary
  view: StageView
  /** The upload row this entry belongs to (pages of a multi-page PDF share it). */
  row: UploadRow
  /** Delete is offered on the upload's own card, not on its sibling pages. */
  deletable: boolean
  counts?: UploadCounts
  estimates: Set<string>
}

const extOf = (name: string) => /\.[a-z0-9]+$/i.exec(name)?.[0]?.toLowerCase()

/** Finished uploads as library entries (one per page for a multi-page PDF), newest first. */
export function ownUploadEntries(rows: UploadRow[], mode: 'full' | 'quick' | null, pageLabel: (n: number) => string): OwnUpload[] {
  const out: OwnUpload[] = []
  for (const r of rows) {
    if (r.phase !== 'ready' || r.leaving) continue
    const st = r.status
    const view = stageView(r.phase, st?.stage, { mode: st?.mode ?? mode ?? undefined, last: r.last, slow: r.slow })
    const base: DrawingSummary = {
      id: r.id,
      title: r.name,
      kind: kindFromName(r.name),
      status: 'ready',
      source: 'upload',
      thumbnail: `/drawings/${encodeURIComponent(r.id)}/thumb.png`,
      credit: { text: r.name, license: '' },
      created_at: r.created_at,
      ...(extOf(r.name) ? { source_ext: extOf(r.name) } : {}),
    }
    const pages = st?.pages ?? []
    const estimates = estimatedCounts(st)
    if (pages.length <= 1) {
      out.push({ summary: base, view, row: r, deletable: Boolean(r.delete_token), counts: st?.counts, estimates })
      continue
    }
    for (const [i, p] of pages.entries()) {
      const id = p.drawing_id ?? (i === 0 ? r.id : undefined)
      if (!id) continue
      const n = p.page ?? i + 1
      const pv = stageView(r.phase, p.stage, { mode: st?.mode ?? mode ?? undefined })
      out.push({
        summary: {
          ...base,
          id,
          thumbnail: `/drawings/${encodeURIComponent(id)}/thumb.png`,
          subtitle: p.label ? `${pageLabel(n)} · ${p.label}` : pageLabel(n),
          floor_label: p.label,
          floor_order: n,
        },
        view: pv,
        row: r,
        deletable: id === r.id && Boolean(r.delete_token),
        counts: p.counts,
        estimates: estimatedCounts(st, p.methods),
      })
    }
  }
  return out.sort((a, b) => (b.row.created_at > a.row.created_at ? 1 : b.row.created_at < a.row.created_at ? -1 : 0))
}

/** Uploads not yet usable (sending, queued, parsing): counted for the sidebar's "N uploading". */
export const inProgressCount = (rows: UploadRow[]) =>
  rows.filter((r) => r.phase === 'uploading' || r.phase === 'queued' || r.phase === 'processing').length

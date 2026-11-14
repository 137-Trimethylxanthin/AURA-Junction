import { ApiError, TransportError, setScopeHeaderProvider } from './client'
import { tu } from '@/i18n/uiLang'
import {
  fastApiMessage,
  isErrorBody,
  isProjectBundle,
  isUploadResult,
  licenceLabel,
  safeUrl,
  type Credit,
  type DrawingKind,
  type DrawingSummary,
  type ProjectBundle,
  type ProjectFloor,
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

// ---- multi-plan house / project bundles & floor auto-matching (1.2) ---------------------------------------

const PROJECTS_KEY = 'aura.projects'

/**
 * Projects are made by the user only (Group plans, or uploading files as one project). Auto-matched bundles are
 * dropped wherever they come from, except the pages of ONE multi-page upload (`up-x`, `up-x-p2`, ...), which are
 * one file's own sheets, not a guess from names.
 */
const UPLOAD_PAGE = /^(up-[A-Za-z0-9_-]+?)(?:-p\d+)?$/
export function isOneUploadPages(p: Pick<ProjectBundle, 'floors'>): boolean {
  const roots = new Set(p.floors.map((f) => UPLOAD_PAGE.exec(f.drawing_id)?.[1] ?? f.drawing_id))
  return p.floors.length > 0 && roots.size === 1 && p.floors.every((f) => UPLOAD_PAGE.test(f.drawing_id))
}
const keepProject = (p: ProjectBundle) => !p.auto_matched || isOneUploadPages(p)
/** Field on a stored bundle holding the ownership token the server returned when it was created. */
const PROJECT_TOKEN_FIELD = 'owner_token'
/** Header the backend reads the project ownership token from; rename here once the backend fixes it. */
export const PROJECT_TOKEN_HEADER = 'x-project-token'
const tokenField = (p: ProjectBundle): string | undefined => {
  const v = (p as unknown as Record<string, unknown>)[PROJECT_TOKEN_FIELD]
  return typeof v === 'string' && v ? v : undefined
}
const tokenHeaders = (projectTokens: string[], deleteTokens: string[]): Record<string, string> => ({
  ...(projectTokens.length ? { [PROJECT_TOKEN_HEADER]: projectTokens.join(',') } : {}),
  ...(deleteTokens.length ? { 'x-delete-token': deleteTokens.join(',') } : {}),
})
const withToken = (p: ProjectBundle, token: string | undefined): ProjectBundle =>
  token ? ({ ...p, [PROJECT_TOKEN_FIELD]: token } as ProjectBundle) : p
const UNGROUPED_PROJECTS_KEY = 'aura.projects.ungrouped'

const projectListeners = new Set<() => void>()
const notifyProjects = () => projectListeners.forEach((fn) => fn())

function sortProjectFloors(floors: readonly ProjectFloor[]): ProjectFloor[] {
  const seen = new Set<string>()
  const unique: ProjectFloor[] = []
  for (const f of floors) {
    if (!f.drawing_id || seen.has(f.drawing_id)) continue
    seen.add(f.drawing_id)
    unique.push({ drawing_id: f.drawing_id, label: f.label.trim() || 'GF', order: f.order })
  }
  return unique.sort((a, b) => a.order - b.order || a.drawing_id.localeCompare(b.drawing_id))
}

function readUngroupedIds(): Set<string> {
  try {
    const v: unknown = JSON.parse(read(UNGROUPED_PROJECTS_KEY) ?? '[]')
    return new Set(Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])
  } catch {
    return new Set()
  }
}

function writeUngroupedIds(ids: Iterable<string>) {
  const arr = [...new Set(ids)]
  write(UNGROUPED_PROJECTS_KEY, arr.length ? JSON.stringify(arr) : null)
}

/**
 * Infer a numeric floor elevation order and short badge label from a drawing title, storey name, or filename.
 * Order convention: basements `< 0` (`B2=-2`, `KG/UG/B1=-1`), ground `0` (`EG/GF/RDC`), upper storeys `1..98`,
 * attic/roof `99` (`DG/Attic/Roof`), or `Page N -> N` when only a sheet/page number is present.
 */
export function inferFloorOrderAndLabel(titleOrFilename: string): { order: number; label: string } {
  const raw = titleOrFilename.trim()
  if (!raw) return { order: 0, label: 'GF' }

  // Strip file extension and random `up-<id>` tokens anywhere in the text (converting `-pN` suffixes to `Page N`).
  const withoutExt = raw.replace(/\.(?:dxf|ifc|pdf|png|jpe?g|webp)$/i, '')
  const strippedUpload = withoutExt
    .replace(/\bup-[a-z0-9]+-p([1-9]\d?)\b/gi, 'Page $1')
    .replace(/\bup-[a-z0-9]+\b/gi, '')
  const norm = strippedUpload.replace(/--|_/g, ' ').trim()

  // 1. Numbered basements: `2. UG`, `2UG`, `B2`, `Basement 2`, `2e sous-sol`
  const numUg = /\b([1-9])\s*\.?\s*(?:ug|untergeschoss|kg|keller|kellergeschoss)\b/i.exec(norm)
  if (numUg) {
    const n = Number(numUg[1])
    const tag = /kg|keller/i.test(numUg[0]) ? 'KG' : 'UG'
    return { order: -n, label: n === 1 ? tag : `${n}. ${tag}` }
  }
  const numB = /\bb([1-9])\b|\bbasement\s*([1-9])?\b/i.exec(norm)
  if (numB) {
    const n = Number(numB[1] ?? numB[2] ?? '1')
    return { order: -n, label: `B${n}` }
  }
  const numSs = /\b([1-9])(?:er|e|ème|eme)?\s*sous[- ]sol\b/i.exec(norm)
  if (numSs) {
    const n = Number(numSs[1])
    return { order: -n, label: `SS${n}` }
  }
  if (/\b(?:kg|keller|kellergeschoss)\b/i.test(norm)) return { order: -1, label: 'KG' }
  if (/\b(?:ug|untergeschoss)\b/i.test(norm)) return { order: -1, label: 'UG' }
  if (/\bsous[- ]sol\b/i.test(norm)) return { order: -1, label: 'SS' }
  if (/\bkellari\b/i.test(norm)) return { order: -1, label: 'Kellari' }
  if (/\b(?:seminterrato|interrato)\b/i.test(norm)) return { order: -1, label: 'S1' }

  // 2. Attic / Roof (`99`)
  if (/\battika\b/i.test(norm)) return { order: 99, label: 'Attika' }
  if (/\b(?:dg|dachgeschoss)\b/i.test(norm)) return { order: 99, label: 'DG' }
  if (/\b(?:attic|penthouse|loft)\b/i.test(norm)) return { order: 99, label: 'Attic' }
  if (/\b(?:roof(?:\s+(?:plan|floor|level))?|rooftop)\b/i.test(norm)) return { order: 99, label: 'Roof' }
  if (/\b(?:combles|toiture)\b/i.test(norm)) return { order: 99, label: 'Combles' }
  if (/\bullakko\b/i.test(norm)) return { order: 99, label: 'Ullakko' }

  // 3. Mezzanine (`0.5`)
  if (/\b(?:mezzanine|zwischengeschoss)\b/i.test(norm)) return { order: 0.5, label: 'MZ' }

  // 4. Ground floor (`0`)
  if (/\b(?:eg|erdgeschoss|parterre)\b/i.test(norm)) return { order: 0, label: 'EG' }
  if (/\b(?:rdc|rez[- ]de[- ]chauss[eé]e|rez)\b/i.test(norm)) return { order: 0, label: 'RDC' }
  if (/\bpohjakerros\b/i.test(norm)) return { order: 0, label: 'PK' }
  if (/\bpiano\s+terra\b/i.test(norm)) return { order: 0, label: 'PT' }
  if (/\b(?:gf|ground(?:\s+(?:floor|level|storey))?)\b/i.test(norm)) return { order: 0, label: 'GF' }
  if (/\b(?:level|floor|storey)\s*0\b|\b0\s*\.?\s*level\b|\b0f\b/i.test(norm)) return { order: 0, label: 'GF' }

  // 5. Upper storeys (`1..98`)
  const deOg = /\b([1-9]\d?)\s*\.?\s*(?:og|obergeschoss)\b/i.exec(norm)
  if (deOg) {
    const n = Number(deOg[1])
    return { order: n, label: `${n}. OG` }
  }
  if (/\b(?:og|obergeschoss)\b/i.test(norm)) return { order: 1, label: '1. OG' }

  const frEt = /\b([1-9]\d?)\s*(?:er|ère|ere|e|ème|eme)?\s*[eé]tage\b/i.exec(norm)
  if (frEt) {
    const n = Number(frEt[1])
    return { order: n, label: n === 1 ? '1er' : `${n}e` }
  }

  const itPi = /\b([1-9]\d?)\s*[°º.]?\s*piano\b/i.exec(norm)
  if (itPi) {
    const n = Number(itPi[1])
    return { order: n, label: `${n}°` }
  }

  const fiKr = /\b([1-9]\d?)\s*\.?\s*(?:kerros|krs)\b/i.exec(norm)
  if (fiKr) {
    const n = Number(fiKr[1])
    return { order: n, label: `${n}. krs` }
  }

  const lvl = /\b(?:level|lvl)\s*([1-9]\d?)\b|\b([1-9]\d?)\s*\.?\s*level\b/i.exec(norm)
  if (lvl) {
    const n = Number(lvl[1] ?? lvl[2])
    return { order: n, label: `Level ${n}` }
  }

  const enFl =
    /\b([1-9]\d?)\s*(?:st|nd|rd|th)?\s*(?:floor|storey|story)\b|\b(?:floor|storey|story)\s*([1-9]\d?)\b|\b([1-9]\d?)f\b/i.exec(
      norm,
    )
  if (enFl) {
    const n = Number(enFl[1] ?? enFl[2] ?? enFl[3])
    return { order: n, label: `${n}F` }
  }

  // 6. Multi-page / sheet numbers (`Page N`, `Sheet N`, `-pN`)
  const pageMatch = /\b(?:page|sheet|seite|sivu|feuille)\s*([1-9]\d?)\b|(?:^|[-_\s])p([1-9]\d?)$/i.exec(norm)
  if (pageMatch) {
    const n = Number(pageMatch[1] ?? pageMatch[2])
    return { order: n, label: `Page ${n}` }
  }

  return { order: 0, label: 'GF' }
}

const GENERIC_STEM_STOPWORDS = new Set([
  'plan',
  'grundriss',
  'pianta',
  'pohjapiirros',
  'floor',
  'floor plan',
  'floorplan',
  'storey',
  'story',
  'level',
  'etage',
  'étage',
  'kerros',
  'piano',
  'sheet',
  'page',
  'seite',
  'sivu',
  'drawing',
  'house',
  'building',
  'project',
  'project name',
  'untitled',
])

function cleanBuildingStem(text: string): { slug: string; title: string } | null {
  let s = text
    .trim()
    .replace(/\.(?:dxf|ifc|pdf|png|jpe?g|webp)$/i, '')
    .replace(/\bup-[a-z0-9]+(?:-p\d+)?\b/gi, ' ')
    .replace(/\b(?:grundriss|floor\s*plan|floorplan|pohjapiirros|plan|pianta)\b/gi, ' ')
    .replace(/\b[1-9]\d?\s*\.?\s*(?:ug|untergeschoss|kg|keller|kellergeschoss|og|obergeschoss|kerros|krs|level)\b/gi, ' ')
    .replace(/\b[1-9]\d?\s*(?:er|ère|ere|e|ème|eme)?\s*[eé]tage\b/gi, ' ')
    .replace(/\b[1-9]\d?\s*[°º.]?\s*piano\b/gi, ' ')
    .replace(/\b(?:[1-9]\d?\s*(?:st|nd|rd|th)?\s*(?:floor|storey|story)|(?:floor|storey|story|level|lvl)\s*\d+|[0-9]\d?f|b[1-9])\b/gi, ' ')
    .replace(
      /\b(?:eg|erdgeschoss|parterre|ug|untergeschoss|kg|keller|kellergeschoss|dg|dachgeschoss|attika|attic|penthouse|loft|roof|rooftop|combles|toiture|ullakko|pohjakerros|kellari|seminterrato|interrato|piano\s+terra|rdc|rez[- ]de[- ]chauss[eé]e|rez|sous[- ]sol|gf|ground(?:\s+floor)?|basement\s*\d*|mezzanine|zwischengeschoss)\b/gi,
      ' ',
    )
    .replace(/\b(?:page|sheet|seite|sivu|feuille)\s*\d+\b|(?:^|[-_\s])p\d+$/gi, ' ')
    .replace(/[·—–\-_:()/[\]]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()

  if (!s || s.length < 3 || /^\d+$/.test(s)) return null
  const lower = s.toLowerCase()
  if (GENERIC_STEM_STOPWORDS.has(lower)) return null
  const slug = lower
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  if (slug.length < 3) return null
  // Title-case all-lowercase filename stems while preserving already mixed-case titles
  if (s === lower) {
    s = s.replace(/\b\w/g, (c) => c.toUpperCase())
  }
  return { slug, title: s }
}

function siteTagOf(d: Pick<DrawingSummary, 'subtitle'>): string | null {
  const m = d.subtitle ? /\bSite\s+(\d+)\b/i.exec(d.subtitle) : null
  return m ? m[1] : null
}

function buildAutoProjectFloors(
  members: readonly DrawingSummary[],
  uploadNames: Record<string, string>,
  isUploadPageGroup: boolean,
): ProjectFloor[] {
  const rawFloors = members.map((d) => {
    if (d.floor_order !== undefined && d.floor_label) {
      return { drawing_id: d.id, label: d.floor_label, order: d.floor_order, explicit: true }
    }
    const sourceText = [uploadNames[d.id], displayDrawingTitle(d), d.subtitle, d.id].filter(Boolean).join(' · ')
    const inf = inferFloorOrderAndLabel(sourceText)
    // First page (`up-<tok>`) of a multi-page PDF often has no `-p1` suffix while siblings are `Page 2`, `Page 3`.
    if (isUploadPageGroup && !/-p\d+$/i.test(d.id) && inf.order === 0 && inf.label === 'GF') {
      return {
        drawing_id: d.id,
        label: d.floor_label ?? 'Page 1',
        order: d.floor_order ?? 1,
        explicit: false,
      }
    }
    return {
      drawing_id: d.id,
      label: d.floor_label ?? inf.label,
      order: d.floor_order ?? inf.order,
      explicit: d.floor_order !== undefined || Boolean(d.floor_label),
    }
  })

  // Disambiguate duplicate fallback labels if every member fell back to the same default
  const allSameDefault =
    rawFloors.length > 1 && rawFloors.every((f) => !f.explicit && f.order === 0 && f.label === 'GF')
  if (allSameDefault) {
    return rawFloors.map((f, i) => ({ drawing_id: f.drawing_id, label: `Page ${i + 1}`, order: i + 1 }))
  }
  return sortProjectFloors(rawFloors)
}

/**
 * Auto-group 2+ related plans into `ProjectBundle`s when they share:
 * 1. An explicit `project_id` from the backend
 * 2. A multi-page upload token (`up-<tok>` + `up-<tok>-p2`...)
 * 3. A multi-storey IFC model stem (`ifc-<stem>--<storey>`)
 * 4. A shared Swiss Dwellings `Site <N>` building identifier
 * 5. A shared non-generic building filename or title stem (e.g. `Villa_Mueller_EG.dxf` + `Villa_Mueller_1OG.dxf`)
 */
export function autoGroupDrawings(
  drawings: readonly DrawingSummary[],
  opts: { uploadNames?: Record<string, string> } = {},
): ProjectBundle[] {
  if (drawings.length < 2) return []

  const uploadNames: Record<string, string> = { ...opts.uploadNames }
  if (!opts.uploadNames) {
    for (const u of myUploads.list()) {
      uploadNames[u.id] = u.name
    }
  }

  const claimed = new Set<string>()
  const bundles: ProjectBundle[] = []

  const emitGroup = (id: string, title: string, members: DrawingSummary[], isUploadPageGroup = false) => {
    const unclaimed = members.filter((m) => !claimed.has(m.id))
    if (unclaimed.length < 2) return
    for (const m of unclaimed) claimed.add(m.id)
    bundles.push({
      id,
      title,
      floors: buildAutoProjectFloors(unclaimed, uploadNames, isUploadPageGroup),
      auto_matched: true,
    })
  }

  // 1. Explicit `project_id`
  const byProjectId = new Map<string, DrawingSummary[]>()
  for (const d of drawings) {
    if (!d.project_id) continue
    const list = byProjectId.get(d.project_id) ?? []
    list.push(d)
    byProjectId.set(d.project_id, list)
  }
  for (const [pid, members] of byProjectId) {
    if (members.length < 2) continue
    const stem = cleanBuildingStem(uploadNames[members[0].id] ?? displayDrawingTitle(members[0]))
    const isUp = members.some((m) => /^up-[a-z0-9]+-p\d+$/i.test(m.id))
    emitGroup(pid, stem?.title ?? displayDrawingTitle(members[0]), members, isUp)
  }

  // 2. Multi-page upload stems (`up-<tok>` + `up-<tok>-p2`...)
  const byUploadStem = new Map<string, DrawingSummary[]>()
  for (const d of drawings) {
    if (claimed.has(d.id)) continue
    const m = /^up-([a-z0-9]+)(?:-p\d+)?$/i.exec(d.id)
    if (!m) continue
    const tok = m[1].toLowerCase()
    const list = byUploadStem.get(tok) ?? []
    list.push(d)
    byUploadStem.set(tok, list)
  }
  for (const [tok, members] of byUploadStem) {
    if (members.length < 2) continue
    const primary = members.find((m) => !/-p\d+$/i.test(m.id)) ?? members[0]
    const stem = cleanBuildingStem(uploadNames[primary.id] ?? displayDrawingTitle(primary))
    emitGroup(`proj-${tok}`, stem?.title ?? displayDrawingTitle(primary), members, true)
  }

  // 3. Multi-storey IFC model stems (`ifc-<stem>--<storey>`)
  const byIfcStem = new Map<string, DrawingSummary[]>()
  for (const d of drawings) {
    if (claimed.has(d.id)) continue
    const m = /^(ifc-[a-z0-9_-]+?)--(.+)$/i.exec(d.id)
    if (!m) continue
    const stemKey = m[1].toLowerCase()
    const list = byIfcStem.get(stemKey) ?? []
    list.push(d)
    byIfcStem.set(stemKey, list)
  }
  for (const [stemKey, members] of byIfcStem) {
    if (members.length < 2) continue
    const disp = displayDrawingTitle(members[0]).split(/\s*·\s*/)[0]?.trim()
    emitGroup(`auto-${stemKey}`, disp || stemKey, members)
  }

  // 4. Shared Swiss Dwellings `Site <N>` building identifier
  const bySite = new Map<string, DrawingSummary[]>()
  for (const d of drawings) {
    if (claimed.has(d.id)) continue
    const site = siteTagOf(d)
    if (!site) continue
    const list = bySite.get(site) ?? []
    list.push(d)
    bySite.set(site, list)
  }
  for (const [site, members] of bySite) {
    if (members.length < 2) continue
    emitGroup(`auto-site-${site}`, `Site ${site}`, members)
  }

  // 5. Shared non-generic house/building filename or title stem (never merging distinct `Site <N>` buildings)
  const byTitleStem = new Map<string, { title: string; members: DrawingSummary[] }>()
  for (const d of drawings) {
    if (claimed.has(d.id) || siteTagOf(d)) continue
    const source = uploadNames[d.id] ?? displayDrawingTitle(d)
    const stem = cleanBuildingStem(source)
    if (!stem) continue
    const cur = byTitleStem.get(stem.slug) ?? { title: stem.title, members: [] }
    cur.members.push(d)
    byTitleStem.set(stem.slug, cur)
  }
  for (const [slug, { title, members }] of byTitleStem) {
    if (members.length < 2) continue
    emitGroup(`auto-stem-${slug}`, title, members)
  }

  return bundles
}

/** Local persistence (`aura.projects`) and auto-match resolution for multi-plan House / Project bundles. */
export const myProjects = {
  list(): ProjectBundle[] {
    return myProjects.listStored().filter(keepProject)
  },

  /** Everything stored, including old auto-matched bundles (kept on disk, never shown). */
  listStored(): ProjectBundle[] {
    try {
      const v: unknown = JSON.parse(read(PROJECTS_KEY) ?? '[]')
      if (!Array.isArray(v)) return []
      return v
        .filter(isProjectBundle)
        .map((p) => ({ ...p, floors: sortProjectFloors(p.floors) }))
        .filter((p) => p.floors.length > 0)
    } catch {
      return []
    }
  },

  get(id: string): ProjectBundle | null {
    return myProjects.list().find((p) => p.id === id) ?? null
  },

  /** Find the saved or auto-matched project bundle containing `drawingId`. */
  forDrawing(
    drawingId: string,
    drawings: readonly DrawingSummary[] = [],
    serverProjects: readonly ProjectBundle[] = [],
  ): ProjectBundle | null {
    const saved = myProjects.list().find((p) => p.floors.some((f) => f.drawing_id === drawingId))
    if (saved) return saved
    if (drawings.length === 0 && serverProjects.length === 0) return null
    return (
      myProjects
        .resolveAll(drawings, serverProjects)
        .find((p) => p.floors.some((f) => f.drawing_id === drawingId)) ?? null
    )
  },

  save(bundle: ProjectBundle): ProjectBundle {
    const normalized: ProjectBundle = {
      ...bundle,
      title: bundle.title.trim() || 'House Project',
      floors: sortProjectFloors(bundle.floors),
      created_at: bundle.created_at ?? new Date().toISOString(),
    }
    const ungrouped = readUngroupedIds()
    if (ungrouped.delete(normalized.id)) writeUngroupedIds(ungrouped)
    const all = myProjects.list()
    const prev = all.find((p) => p.id === normalized.id)
    const kept = tokenField(normalized) ? normalized : withToken(normalized, prev && tokenField(prev))
    const rest = all.filter((p) => p.id !== normalized.id)
    write(PROJECTS_KEY, JSON.stringify([kept, ...rest]))
    notifyProjects()
    return kept
  },

  /** The ownership token stored with a project (returned by the server when it was created). */
  tokenOf(id: string): string | undefined {
    const p = myProjects.get(id)
    return p ? tokenField(p) : undefined
  },

  /** Keep the token the server returned for a project alongside it in `aura.projects`. */
  setToken(id: string, token: string | undefined) {
    if (!token) return
    const all = myProjects.list()
    if (!all.some((p) => p.id === id)) return
    write(PROJECTS_KEY, JSON.stringify(all.map((p) => (p.id === id ? withToken(p, token) : p))))
  },

  /**
   * Ownership headers for updating or deleting a project on the server: its stored token, else the
   * delete token of one of this browser's uploads among its floors. Empty when neither is known.
   */
  authHeaders(bundle: ProjectBundle): Record<string, string> {
    const token = tokenField(bundle) ?? myProjects.tokenOf(bundle.id)
    // The backend needs the delete token of EVERY upload floor to save a bundle over uploads.
    const uploads = new Map(myUploads.list().map((u) => [u.id, u.delete_token]))
    const floorTokens = [...new Set(bundle.floors.map((f) => uploads.get(f.drawing_id)).filter((t): t is string => !!t))]
    return tokenHeaders(token ? [token] : [], floorTokens)
  },

  /**
   * Credentials for `GET /projects`: every stored project token and every upload delete token of this
   * browser (comma-joined), so bundles over the user's own uploads are listed. Empty lists are omitted.
   */
  listHeaders(): Record<string, string> {
    const projectTokens = myProjects.list().map(tokenField).filter((t): t is string => !!t)
    return tokenHeaders([...new Set(projectTokens)], [...new Set(myUploads.list().map((u) => u.delete_token))])
  },

  /**
   * Drop floors whose drawing is gone (expired uploads 404 after 24 h) and any project left with
   * fewer than 2 floors. Returns how many projects changed.
   */
  pruneFloors(missing: Iterable<string>): number {
    const gone = new Set(missing)
    if (gone.size === 0) return 0
    let changed = 0
    const next: ProjectBundle[] = []
    for (const p of myProjects.list()) {
      const floors = p.floors.filter((f) => !gone.has(f.drawing_id))
      if (floors.length === p.floors.length) {
        next.push(p)
        continue
      }
      changed++
      if (floors.length >= 2) next.push({ ...p, floors })
    }
    if (changed) {
      write(PROJECTS_KEY, JSON.stringify(next))
      notifyProjects()
    }
    return changed
  },

  /** Check every stored floor once with `isMissing` (true only for a definite 404) and prune the gone ones. */
  async pruneExpired(isMissing: (drawingId: string) => Promise<boolean>): Promise<number> {
    const ids = [...new Set(myProjects.list().flatMap((p) => p.floors.map((f) => f.drawing_id)))]
    const results = await Promise.all(ids.map(async (id) => ((await isMissing(id).catch(() => false)) ? id : null)))
    return myProjects.pruneFloors(results.filter((x): x is string => x !== null))
  },

  /** Convenience builder for grouping 2+ plans into a named user project bundle. */
  create(
    title: string,
    drawings: readonly (Pick<DrawingSummary, 'id' | 'title'> & Partial<Pick<DrawingSummary, 'floor_label' | 'floor_order'>>)[],
    id = `proj-local-${Date.now().toString(36)}`,
  ): ProjectBundle {
    const floors: ProjectFloor[] = drawings.map((d) => {
      const inf = inferFloorOrderAndLabel(d.title)
      return {
        drawing_id: d.id,
        label: d.floor_label ?? inf.label,
        order: d.floor_order ?? inf.order,
      }
    })
    return myProjects.save({ id, title, floors, auto_matched: false })
  },

  remove(id: string) {
    const ungrouped = readUngroupedIds()
    ungrouped.add(id)
    writeUngroupedIds(ungrouped)
    write(PROJECTS_KEY, JSON.stringify(myProjects.list().filter((p) => p.id !== id)))
    notifyProjects()
  },

  clear() {
    write(PROJECTS_KEY, null)
    write(UNGROUPED_PROJECTS_KEY, null)
    notifyProjects()
  },

  /** Merge server `/projects` into local storage without clobbering local manual edits or ungrouped tombstones. */
  syncFromServer(serverProjects: readonly ProjectBundle[]): ProjectBundle[] {
    const ungrouped = readUngroupedIds()
    const localById = new Map(myProjects.list().map((p) => [p.id, p]))
    let changed = false
    for (const sp of serverProjects) {
      if (!isProjectBundle(sp) || !keepProject(sp)) continue
      // An Ungroup tombstone is the user's explicit choice: it holds for every server project, including the
      // ones the server derives from files (an IFC model's storeys), which it cannot delete.
      if (ungrouped.has(sp.id)) continue
      const existing = localById.get(sp.id)
      if (existing && !existing.auto_matched) continue
      const normalized = withToken({ ...sp, floors: sortProjectFloors(sp.floors) }, existing && tokenField(existing))
      if (!existing || JSON.stringify(existing) !== JSON.stringify(normalized)) {
        localById.set(sp.id, normalized)
        changed = true
      }
    }
    const merged = [...localById.values()]
    if (changed) {
      write(PROJECTS_KEY, JSON.stringify(merged))
      notifyProjects()
    }
    return merged
  },

  /**
   * Unified list of active multi-floor (`>= 2` floors) project bundles across local user bundles,
   * server `/projects`, and client `autoGroupDrawings(drawings)`, preventing duplicate floor membership.
   */
  resolveAll(
    drawings: readonly DrawingSummary[],
    serverProjects: readonly ProjectBundle[] = [],
  ): ProjectBundle[] {
    const ungrouped = readUngroupedIds()
    const claimedDrawings = new Set<string>()
    const seenIds = new Set<string>()
    const result: ProjectBundle[] = []

    const consider = (p: ProjectBundle, respectTombstone: boolean) => {
      if (seenIds.has(p.id)) return
      if (respectTombstone && ungrouped.has(p.id)) return
      const availableFloors = sortProjectFloors(p.floors).filter((f) => !claimedDrawings.has(f.drawing_id))
      if (availableFloors.length < 2) return
      seenIds.add(p.id)
      for (const f of availableFloors) claimedDrawings.add(f.drawing_id)
      result.push({ ...p, floors: availableFloors })
    }

    void drawings // auto-grouping by file names is gone; the argument stays for callers
    for (const p of myProjects.list()) consider(p, Boolean(p.auto_matched))
    for (const p of serverProjects) if (isProjectBundle(p) && keepProject(p)) consider(p, true)

    return result
  },

  subscribe(fn: () => void) {
    projectListeners.add(fn)
    return () => {
      projectListeners.delete(fn)
    }
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

// A question across a project carries the proof the server asks for: the project's own token and the
// delete token of each upload floor (also when the project lives only in this browser).
setScopeHeaderProvider((scope) => {
  const sc = scope as { kind?: unknown; project_id?: unknown; drawing_ids?: unknown } | null | undefined
  if (!sc || sc.kind !== 'project' || typeof sc.project_id !== 'string') return {}
  const ids = Array.isArray(sc.drawing_ids) ? sc.drawing_ids.filter((d): d is string => typeof d === 'string') : []
  const stored = myProjects.get(sc.project_id)
  return myProjects.authHeaders({
    ...(stored ?? { id: sc.project_id, title: '', auto_matched: false }),
    floors: [
      ...(stored?.floors ?? []),
      ...ids.map((drawing_id, i) => ({ drawing_id, label: '', order: i })),
    ],
  } as ProjectBundle)
})

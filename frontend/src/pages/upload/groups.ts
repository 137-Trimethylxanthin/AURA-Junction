import { useEffect, useRef } from 'react'
import { saveProject } from '@/api/client'
import type { ProjectBundle } from '@/api/contract'
import { inferFloorOrderAndLabel, myProjects } from '@/api/library'
import type { UploadRow } from './useUploads'

/**
 * Uploading several files "as one project". The backend can only bundle drawings that exist (POST /projects
 * checks every floor), so the group is remembered here until its files are ready, then saved as a project.
 * `projectId` adds the files to an existing project instead of making a new one.
 */
export interface PendingGroup {
  id: string
  title: string
  uploadIds: string[]
  projectId?: string
  created_at: string
}

const KEY = 'aura.pendingGroups'

function read(): PendingGroup[] {
  try {
    const v: unknown = JSON.parse(globalThis.localStorage?.getItem(KEY) ?? '[]')
    return Array.isArray(v) ? (v as PendingGroup[]).filter((g) => g && typeof g.id === 'string' && Array.isArray(g.uploadIds)) : []
  } catch {
    return []
  }
}
function write(list: PendingGroup[]) {
  try {
    globalThis.localStorage?.setItem(KEY, JSON.stringify(list))
  } catch {
    /* storage unavailable: the group lives for this page only */
  }
}

export const pendingGroups = {
  list: read,
  add(g: PendingGroup) {
    write([...read().filter((x) => x.id !== g.id), g])
  },
  remove(id: string) {
    write(read().filter((x) => x.id !== id))
  },
}

/** Default project name: the folder the files came from, else the common start of the file names, else the first name. */
export function defaultGroupName(files: { name: string; webkitRelativePath?: string }[]): string {
  const folder = files[0]?.webkitRelativePath?.split('/')[0]
  if (folder && files.every((f) => f.webkitRelativePath?.startsWith(folder + '/'))) return folder
  const stems = files.map((f) => f.name.replace(/\.[^.]+$/, ''))
  let prefix = stems[0] ?? ''
  for (const s of stems.slice(1)) while (prefix && !s.startsWith(prefix)) prefix = prefix.slice(0, -1)
  prefix = prefix.replace(/[\s_\-.(]+$/, '')
  return prefix.length >= 3 ? prefix : (stems[0] ?? 'Project')
}

/** What to do with a group whose uploads have moved on: wait, save it, or drop it (fewer than 2 usable sheets). */
export function groupDecision(g: PendingGroup, rows: UploadRow[]): { kind: 'wait' } | { kind: 'save'; ready: UploadRow[] } | { kind: 'drop' } {
  const members = g.uploadIds.map((id) => rows.find((r) => r.id === id))
  const ready = members.filter((r): r is UploadRow => r?.phase === 'ready')
  const pending = members.some((r) => r && (r.phase === 'uploading' || r.phase === 'queued' || r.phase === 'processing'))
  if (pending) return { kind: 'wait' }
  const existing = g.projectId ? myProjects.get(g.projectId) : null
  if (ready.length + (existing?.floors.length ?? 0) < 2) return { kind: 'drop' }
  return { kind: 'save', ready }
}

/** Builds the project (or the extended one) from the ready sheets; labels are prefilled from the file names. */
export function bundleFor(g: PendingGroup, ready: UploadRow[]): ProjectBundle {
  const existing = g.projectId ? myProjects.get(g.projectId) : null
  const floors = [...(existing?.floors ?? [])]
  for (const [i, r] of ready.entries()) {
    if (floors.some((f) => f.drawing_id === r.id)) continue
    const inf = inferFloorOrderAndLabel(r.name.replace(/\.[^.]+$/, ''))
    floors.push({ drawing_id: r.id, label: inf.label, order: Number.isFinite(inf.order) ? inf.order : floors.length + i + 1 })
  }
  return existing
    ? myProjects.save({ ...existing, floors })
    : myProjects.save({ id: `proj-up-${g.id}`, title: g.title, floors, auto_matched: false })
}

/** Saves each pending group as a project once its files are ready (locally at once, then on the server). */
export function useGroupFinisher(base: string | null, rows: UploadRow[]) {
  const busy = useRef(new Set<string>())
  useEffect(() => {
    for (const g of pendingGroups.list()) {
      if (busy.current.has(g.id)) continue
      const d = groupDecision(g, rows)
      if (d.kind === 'wait') continue
      if (d.kind === 'drop') {
        pendingGroups.remove(g.id)
        continue
      }
      busy.current.add(g.id)
      const bundle = bundleFor(g, d.ready)
      pendingGroups.remove(g.id)
      if (!base) {
        busy.current.delete(g.id)
        continue
      }
      void saveProject({ baseUrl: base }, bundle, { headers: myProjects.authHeaders(bundle) })
        .then(({ token }) => myProjects.setToken(bundle.id, token))
        .catch(() => {
          /* kept locally; the library's project bar or source.chat syncs it later */
        })
        .finally(() => busy.current.delete(g.id))
    }
  }, [base, rows])
}

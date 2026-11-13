import type { AskResponse, Drawing } from '@/api/contract'
import { isBoxRejected, isRoomIncluded, roomEvidenceId, type Toggles } from '@/lib/answer'

const COLUMNS = ['n', 'id', 'subject', 'room', 'unit', 'x', 'y', 'w', 'h', 'state'] as const

const q = (v: string | number | undefined) => {
  const s = v === undefined ? '' : String(v)
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

const r2 = (n: number) => Math.round(n * 100) / 100

/** Evidence of one answer as CSV. Positions are % of the rendered plan image; rooms use their polygon's bounding box. */
export function evidenceCsv(resp: AskResponse, toggles: Toggles, drawing: Drawing): string {
  const byId = new Map(drawing.rooms.map((r) => [r.room_id, r]))
  const lines: string[] = [COLUMNS.join(',')]
  resp.evidence.boxes.forEach((b, i) => {
    lines.push(
      [i + 1, b.id, b.subject, b.room_id, b.unit, r2(b.x), r2(b.y), r2(b.w), r2(b.h), isBoxRejected(b.id, toggles) ? 'rejected' : 'counted']
        .map(q)
        .join(','),
    )
  })
  resp.evidence.rooms.forEach((r, i) => {
    const poly = byId.get(r.room_id)
    const xs = poly?.points.map((p) => p[0]) ?? []
    const ys = poly?.points.map((p) => p[1]) ?? []
    const x = xs.length ? Math.min(...xs) : undefined
    const y = ys.length ? Math.min(...ys) : undefined
    lines.push(
      [
        resp.evidence.boxes.length + i + 1,
        roomEvidenceId(r.room_id),
        'room',
        r.room_id,
        poly?.unit,
        x === undefined ? undefined : r2(x),
        y === undefined ? undefined : r2(y),
        x === undefined ? undefined : r2(Math.max(...xs) - x),
        y === undefined ? undefined : r2(Math.max(...ys) - y),
        isRoomIncluded(r, toggles) ? 'included' : 'left_out',
      ]
        .map(q)
        .join(','),
    )
  })
  return lines.join('\r\n') + '\r\n'
}

export function downloadText(filename: string, text: string, type = 'text/csv;charset=utf-8') {
  const url = URL.createObjectURL(new Blob(['﻿', text], { type }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

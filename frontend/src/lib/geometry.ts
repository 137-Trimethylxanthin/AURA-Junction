import type { Pct, PctRect, RoomPoly } from '@/api/contract'

/** Even-odd ray casting; points in % of the image. */
export function pointInPolygon(x: Pct, y: Pct, pts: readonly (readonly [number, number])[]): boolean {
  let inside = false
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [xi, yi] = pts[i]
    const [xj, yj] = pts[j]
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

export function roomAt(rooms: readonly RoomPoly[], x: Pct, y: Pct): RoomPoly | null {
  return rooms.find((r) => pointInPolygon(x, y, r.points)) ?? null
}

/** Room under the point, else the room whose label is closest, if within `maxDist` %. */
export function nearestRoom(rooms: readonly RoomPoly[], x: Pct, y: Pct, maxDist = 6): RoomPoly | null {
  const hit = roomAt(rooms, x, y)
  if (hit) return hit
  let best: RoomPoly | null = null
  let bestDist = Infinity
  for (const r of rooms) {
    const d = Math.hypot(r.label_at[0] - x, r.label_at[1] - y)
    if (d < bestDist) {
      bestDist = d
      best = r
    }
  }
  return bestDist < maxDist ? best : null
}

export function pointInRect(x: Pct, y: Pct, r: PctRect): boolean {
  return x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h
}

export function boxCentreInRect(b: PctRect, r: PctRect): boolean {
  return pointInRect(b.x + b.w / 2, b.y + b.h / 2, r)
}

/** Rect with non-negative width/height (a drag can go up or left). */
export function normaliseRect(r: PctRect): PctRect {
  return {
    x: r.w < 0 ? r.x + r.w : r.x,
    y: r.h < 0 ? r.y + r.h : r.y,
    w: Math.abs(r.w),
    h: Math.abs(r.h),
  }
}

/** Rect spanned by two points. */
export function rectFromPoints(a: [Pct, Pct], b: [Pct, Pct]): PctRect {
  return normaliseRect({ x: a[0], y: a[1], w: b[0] - a[0], h: b[1] - a[1] })
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

/** Normalised and clipped to the 0–100 % image. */
export function clampRect(r: PctRect): PctRect {
  const n = normaliseRect(r)
  const x0 = clamp(n.x, 0, 100)
  const y0 = clamp(n.y, 0, 100)
  const x1 = clamp(n.x + n.w, 0, 100)
  const y1 = clamp(n.y + n.h, 0, 100)
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}

export function bboxOf(pts: readonly (readonly [number, number])[]): PctRect {
  const xs = pts.map((p) => p[0])
  const ys = pts.map((p) => p[1])
  const x = Math.min(...xs)
  const y = Math.min(...ys)
  return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y }
}

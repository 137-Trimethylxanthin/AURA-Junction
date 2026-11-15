/**
 * The footprint of a plan for its base board: the rooms and walls grown by a margin, holes filled, as axis-aligned
 * strips. An L-shaped plan then stands on an L-shaped board instead of on the rectangle around it.
 */
export interface Strip {
  x0: number
  x1: number
  z0: number
  z1: number
}

type Poly = ArrayLike<{ x: number; y: number }>

/**
 * `polys`: closed outlines in world (x, z) metres (as Vector2: y is z). Returns strips covering every point within
 * `margin` of an outline's inside (to the resolution of the grid), never reaching past the bounding box grown by the
 * margin. Empty when there is nothing to stand on.
 */
export function footprintStrips(polys: Poly[], margin: number, maxCells = 320): Strip[] {
  let minX = Infinity
  let maxX = -Infinity
  let minZ = Infinity
  let maxZ = -Infinity
  for (const p of polys)
    for (let i = 0; i < p.length; i++) {
      const v = p[i]
      if (!Number.isFinite(v.x) || !Number.isFinite(v.y)) continue
      if (v.x < minX) minX = v.x
      if (v.x > maxX) maxX = v.x
      if (v.y < minZ) minZ = v.y
      if (v.y > maxZ) maxZ = v.y
    }
  if (!(maxX > minX) || !(maxZ > minZ)) return []
  const cell = Math.max(Math.max(maxX - minX, maxZ - minZ) / maxCells, 0.02)
  const k = Math.ceil(margin / cell + 0.5)
  const ox = minX - (k + 1) * cell
  const oz = minZ - (k + 1) * cell
  const nx = Math.ceil((maxX - minX) / cell) + 2 * (k + 1)
  const nz = Math.ceil((maxZ - minZ) / cell) + 2 * (k + 1)
  let grid = new Uint8Array(nx * nz)

  // scanline fill of each outline (cell centres inside it)
  const xs: number[] = []
  for (const p of polys) {
    const n = p.length
    if (n < 3) continue
    let z0 = Infinity
    let z1 = -Infinity
    for (let i = 0; i < n; i++) {
      if (p[i].y < z0) z0 = p[i].y
      if (p[i].y > z1) z1 = p[i].y
    }
    const r0 = Math.max(0, Math.floor((z0 - oz) / cell - 0.5))
    const r1 = Math.min(nz - 1, Math.ceil((z1 - oz) / cell - 0.5))
    for (let r = r0; r <= r1; r++) {
      const z = oz + (r + 0.5) * cell
      xs.length = 0
      for (let i = 0, j = n - 1; i < n; j = i++) {
        const a = p[i]
        const b = p[j]
        if (a.y > z !== b.y > z) xs.push(((b.x - a.x) * (z - a.y)) / (b.y - a.y) + a.x)
      }
      xs.sort((u, v) => u - v)
      for (let q = 0; q + 1 < xs.length; q += 2) {
        const c0 = Math.max(0, Math.ceil((xs[q] - ox) / cell - 0.5))
        const c1 = Math.min(nx - 1, Math.floor((xs[q + 1] - ox) / cell - 0.5))
        for (let c = c0; c <= c1; c++) grid[r * nx + c] = 1
      }
    }
  }

  // grow by k cells: along x, then along z
  const grow = (src: Uint8Array, alongX: boolean) => {
    const out = new Uint8Array(src.length)
    const lines = alongX ? nz : nx
    const len = alongX ? nx : nz
    for (let l = 0; l < lines; l++) {
      let last = -Infinity
      const at = (i: number) => (alongX ? l * nx + i : i * nx + l)
      for (let i = 0; i < len; i++) {
        if (src[at(i)]) last = i
        if (i - last <= k) out[at(i)] = 1
      }
      last = Infinity
      for (let i = len - 1; i >= 0; i--) {
        if (src[at(i)]) last = i
        if (last - i <= k) out[at(i)] = 1
      }
    }
    return out
  }
  grid = grow(grow(grid, true), false)

  // fill holes: empty cells the outside cannot reach
  const outside = new Uint8Array(nx * nz)
  const stack: number[] = []
  const visit = (i: number) => {
    if (!grid[i] && !outside[i]) {
      outside[i] = 1
      stack.push(i)
    }
  }
  for (let c = 0; c < nx; c++) (visit(c), visit((nz - 1) * nx + c))
  for (let r = 0; r < nz; r++) (visit(r * nx), visit(r * nx + nx - 1))
  while (stack.length) {
    const i = stack.pop()!
    const c = i % nx
    if (c > 0) visit(i - 1)
    if (c < nx - 1) visit(i + 1)
    if (i >= nx) visit(i - nx)
    if (i < nx * (nz - 1)) visit(i + nx)
  }

  // strips: runs of a row, merged with the same run of the rows below
  const lim = { x0: minX - margin, x1: maxX + margin, z0: minZ - margin, z1: maxZ + margin }
  const strips: Strip[] = []
  const open = new Map<string, Strip>()
  for (let r = 0; r < nz; r++) {
    const seen = new Set<string>()
    for (let c = 0; c < nx; ) {
      if (outside[r * nx + c]) {
        c++
        continue
      }
      let e = c
      while (e < nx && !outside[r * nx + e]) e++
      const key = `${c}:${e}`
      seen.add(key)
      const z1 = Math.min(lim.z1, oz + (r + 1) * cell)
      const cur = open.get(key)
      if (cur) cur.z1 = z1
      else {
        const s = { x0: Math.max(lim.x0, ox + c * cell), x1: Math.min(lim.x1, ox + e * cell), z0: Math.max(lim.z0, oz + r * cell), z1 }
        open.set(key, s)
        strips.push(s)
      }
      c = e
    }
    for (const key of [...open.keys()]) if (!seen.has(key)) open.delete(key)
  }
  return strips.filter((s) => s.x1 > s.x0 && s.z1 > s.z0)
}

import * as THREE from 'three'

/**
 * A raster drawing in 3D, without pretending to know more than the pixels:
 *  - relief: the sheet lies on the table and its ink is raised, so line work catches the light;
 *  - walls:  strokes at least `minStroke` px thick (after downscaling) are extruded as walls; thinner ink
 *            (text, hatching, dimensions) stays as relief. Meant for floor plans; on elevations it just
 *            extrudes the heavy outlines, and the UI says the heights are illustrative.
 * Pure pixel maths (ink mask, opening, greedy rectangles) lives in `inkMask` / `wallRects` and is unit-tested.
 */
export type ImageMode = 'relief' | 'walls'

export interface ImageModel {
  group: THREE.Group
  size: { w: number; d: number; radius: number }
  setMode(mode: ImageMode): void
  setWallHeight(h: number): void
  setRelief(depth: number): void
  /** Minimum stroke width (px of the downscaled image) that counts as a wall; rebuilds the walls. */
  setMinStroke(px: number): void
  dispose(): void
}

export const MAX_SIDE = 1400

export interface Raster {
  w: number
  h: number
  /** RGBA, row-major */
  data: Uint8ClampedArray
}

/** Downscaled pixels of an image (long side at most MAX_SIDE). */
export function rasterOf(img: HTMLImageElement | ImageBitmap, maxSide = MAX_SIDE): Raster {
  const iw = 'naturalWidth' in img ? img.naturalWidth : img.width
  const ih = 'naturalHeight' in img ? img.naturalHeight : img.height
  const k = Math.min(1, maxSide / Math.max(iw, ih))
  const w = Math.max(2, Math.round(iw * k))
  const h = Math.max(2, Math.round(ih * k))
  const c = document.createElement('canvas')
  c.width = w
  c.height = h
  const ctx = c.getContext('2d', { willReadFrequently: true })
  if (!ctx) throw new Error('2D canvas unavailable')
  ctx.drawImage(img, 0, 0, w, h)
  return { w, h, data: ctx.getImageData(0, 0, w, h).data }
}

/** Box blur of a float field via an integral image (radius r). */
function boxBlur(src: Float32Array, w: number, h: number, r: number): Float32Array {
  const I = new Float64Array((w + 1) * (h + 1))
  for (let y = 0; y < h; y++) {
    let row = 0
    for (let x = 0; x < w; x++) {
      row += src[y * w + x]
      I[(y + 1) * (w + 1) + x + 1] = I[y * (w + 1) + x + 1] + row
    }
  }
  const out = new Float32Array(w * h)
  for (let y = 0; y < h; y++) {
    const y0 = Math.max(0, y - r)
    const y1 = Math.min(h, y + r + 1)
    for (let x = 0; x < w; x++) {
      const x0 = Math.max(0, x - r)
      const x1 = Math.min(w, x + r + 1)
      const s = I[y1 * (w + 1) + x1] - I[y0 * (w + 1) + x1] - I[y1 * (w + 1) + x0] + I[y0 * (w + 1) + x0]
      out[y * w + x] = s / ((x1 - x0) * (y1 - y0))
    }
  }
  return out
}

/**
 * Ink strength 0–1 per pixel. The paper is estimated locally (a wide blur), so stains, fading and blueprint
 * gradients do not count as ink. Polarity is decided globally: dark paper (blueprints) means light ink.
 */
export function inkMask(r: Raster): { ink: Float32Array; darkPaper: boolean } {
  const { w, h, data } = r
  const L = new Float32Array(w * h)
  let sum = 0
  for (let i = 0; i < w * h; i++) {
    const a = data[i * 4 + 3] / 255
    // Transparent pixels read as white paper.
    const l = (0.2126 * data[i * 4] + 0.7152 * data[i * 4 + 1] + 0.0722 * data[i * 4 + 2]) / 255
    L[i] = l * a + (1 - a)
    sum += L[i]
  }
  const darkPaper = sum / (w * h) < 0.5
  const bg = boxBlur(L, w, h, Math.max(6, Math.round(Math.max(w, h) / 40)))
  const ink = new Float32Array(w * h)
  for (let i = 0; i < w * h; i++) {
    const diff = darkPaper ? L[i] - bg[i] : bg[i] - L[i]
    ink[i] = Math.max(0, Math.min(1, (diff - 0.06) / 0.22))
  }
  return { ink, darkPaper }
}

/** Morphological opening (erode then dilate, square kernel) of a binary mask: keeps strokes ≥ 2r+1 px. */
export function openMask(mask: Uint8Array, w: number, h: number, r: number): Uint8Array {
  if (r <= 0) return mask.slice()
  const pass = (src: Uint8Array, erode: boolean) => {
    const tmp = new Uint8Array(w * h)
    const out = new Uint8Array(w * h)
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        let v = erode ? 1 : 0
        for (let k = -r; k <= r; k++) {
          const xx = x + k
          const s = xx < 0 || xx >= w ? (erode ? 0 : 0) : src[y * w + xx]
          if (erode ? !s : s) {
            v = erode ? 0 : 1
            break
          }
        }
        tmp[y * w + x] = v
      }
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        let v = erode ? 1 : 0
        for (let k = -r; k <= r; k++) {
          const yy = y + k
          const s = yy < 0 || yy >= h ? 0 : tmp[yy * w + x]
          if (erode ? !s : s) {
            v = erode ? 0 : 1
            break
          }
        }
        out[y * w + x] = v
      }
    return out
  }
  return pass(pass(mask, true), false)
}

/** Greedy merge of mask pixels into axis-aligned rectangles [x, y, w, h] (px). */
export function wallRects(mask: Uint8Array, w: number, h: number): [number, number, number, number][] {
  const used = new Uint8Array(w * h)
  const out: [number, number, number, number][] = []
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const i = y * w + x
      if (!mask[i] || used[i]) continue
      let rw = 1
      while (x + rw < w && mask[i + rw] && !used[i + rw]) rw++
      let rh = 1
      grow: while (y + rh < h) {
        for (let k = 0; k < rw; k++) {
          const j = (y + rh) * w + x + k
          if (!mask[j] || used[j]) break grow
        }
        rh++
      }
      for (let yy = y; yy < y + rh; yy++) used.fill(1, yy * w + x, yy * w + x + rw)
      out.push([x, y, rw, rh])
    }
  return out
}

/**
 * Thick-stroke mask for the walls mode: strokes at least `minStroke` px wide, then only the connected pieces
 * shaped like walls. A wall run is long and thin (elongated) or encloses rooms (sparse in its bounding box);
 * a compact solid blob (filled symbol, hatching, a dark photo patch) is dropped.
 */
export function wallMask(ink: Float32Array, w: number, h: number, minStroke: number): Uint8Array {
  const bin = new Uint8Array(w * h)
  for (let i = 0; i < w * h; i++) bin[i] = ink[i] > 0.45 ? 1 : 0
  const open = openMask(bin, w, h, Math.max(1, Math.floor(minStroke / 2)))
  return keepWallLike(open, w, h, minStroke)
}

export function keepWallLike(mask: Uint8Array, w: number, h: number, minStroke: number): Uint8Array {
  const label = new Int32Array(w * h).fill(-1)
  const out = new Uint8Array(w * h)
  const stack: number[] = []
  const members: number[] = []
  const minArea = 6 * minStroke * minStroke
  for (let start = 0; start < w * h; start++) {
    if (!mask[start] || label[start] >= 0) continue
    members.length = 0
    stack.push(start)
    label[start] = start
    let x0 = w
    let y0 = h
    let x1 = 0
    let y1 = 0
    while (stack.length) {
      const i = stack.pop()!
      members.push(i)
      const x = i % w
      const y = (i - x) / w
      if (x < x0) x0 = x
      if (x > x1) x1 = x
      if (y < y0) y0 = y
      if (y > y1) y1 = y
      const visit = (j: number) => {
        if (mask[j] && label[j] < 0) {
          label[j] = start
          stack.push(j)
        }
      }
      if (x > 0) visit(i - 1)
      if (x < w - 1) visit(i + 1)
      if (y > 0) visit(i - w)
      if (y < h - 1) visit(i + w)
    }
    const bw = x1 - x0 + 1
    const bh = y1 - y0 + 1
    const area = members.length
    const elongation = Math.max(bw, bh) / Math.min(bw, bh)
    const fill = area / (bw * bh)
    if (area >= minArea && (elongation >= 3 || fill <= 0.45)) for (const i of members) out[i] = 1
  }
  return out
}

export interface ImageModelOptions {
  /** Width of the sheet in metres. Known for contract drawings (m_per_pct_x * 100); otherwise assumed. */
  widthM: number
  minStroke: number
}

export function buildImageModel(img: HTMLImageElement | ImageBitmap, opts: ImageModelOptions): ImageModel {
  const r = rasterOf(img)
  const { ink, darkPaper } = inkMask(r)
  const w = opts.widthM
  const d = (opts.widthM * r.h) / r.w
  const group = new THREE.Group()
  group.name = 'image'
  const disposables: { dispose(): void }[] = []
  const track = <T extends { dispose(): void }>(x: T) => (disposables.push(x), x)

  const tex = track(new THREE.Texture(img as HTMLImageElement))
  tex.colorSpace = THREE.SRGBColorSpace
  tex.anisotropy = 8
  tex.needsUpdate = true

  // Sheet: a grid displaced by the ink field (heights in metres, scaled by setRelief).
  const seg = Math.min(r.w - 1, 512)
  const segY = Math.max(1, Math.round((seg * r.h) / r.w))
  const geo = track(new THREE.PlaneGeometry(w, d, seg, segY))
  geo.rotateX(-Math.PI / 2)
  const pos = geo.getAttribute('position') as THREE.BufferAttribute
  const inkAt = new Float32Array(pos.count)
  for (let i = 0; i < pos.count; i++) {
    const u = (pos.getX(i) + w / 2) / w
    const v = (pos.getZ(i) + d / 2) / d
    const px = Math.min(r.w - 1, Math.round(u * (r.w - 1)))
    const py = Math.min(r.h - 1, Math.round(v * (r.h - 1)))
    inkAt[i] = ink[py * r.w + px]
  }
  const mat = track(new THREE.MeshStandardMaterial({ map: tex, roughness: 0.9, side: THREE.DoubleSide }))
  const sheet = new THREE.Mesh(geo, mat)
  sheet.receiveShadow = true
  sheet.castShadow = true
  group.add(sheet)

  // Board under the sheet.
  const board = new THREE.Mesh(
    track(new THREE.BoxGeometry(w * 1.04, 0.1, d * 1.04)),
    track(new THREE.MeshStandardMaterial({ color: darkPaper ? '#1c2a3a' : '#fbfbfa', roughness: 0.95 })),
  )
  board.position.y = -0.055
  board.receiveShadow = true
  group.add(board)

  // Walls from thick strokes, built lazily the first time the mode is used.
  const walls = new THREE.Group()
  group.add(walls)
  let wallsBuilt = false
  let wallH = 2.6
  let minStroke = opts.minStroke
  const wallMat = track(new THREE.MeshStandardMaterial({ color: '#f3f1ec', roughness: 0.9 }))
  const clearWalls = () => {
    for (const c of [...walls.children]) {
      ;(c as THREE.Mesh).geometry.dispose()
      walls.remove(c)
    }
  }
  const buildWalls = () => {
    wallsBuilt = true
    clearWalls()
    const mask = wallMask(ink, r.w, r.h, minStroke)
    const rects = wallRects(mask, r.w, r.h)
    const sx = w / r.w
    const sz = d / r.h
    const boxes: THREE.BufferGeometry[] = []
    for (const [x, y, rw, rh] of rects) {
      const b = new THREE.BoxGeometry(rw * sx, 1, rh * sz)
      b.translate((x + rw / 2) * sx - w / 2, 0.5, (y + rh / 2) * sz - d / 2)
      boxes.push(b)
    }
    if (!boxes.length) return
    const merged = mergeBoxes(boxes)
    for (const b of boxes) b.dispose()
    const m = new THREE.Mesh(merged, wallMat)
    m.castShadow = true
    m.receiveShadow = true
    walls.add(m)
  }

  let mode: ImageMode = 'relief'
  let relief = 0.06
  const applyRelief = () => {
    // In walls mode the sheet flattens so the extruded walls read clearly.
    const amp = mode === 'walls' ? relief * 0.25 : relief
    for (let i = 0; i < pos.count; i++) pos.setY(i, inkAt[i] * amp)
    pos.needsUpdate = true
    geo.computeVertexNormals()
  }

  const model: ImageModel = {
    group,
    size: { w, d, radius: Math.hypot(w, d) / 2 },
    setMode(m) {
      mode = m
      if (m === 'walls' && !wallsBuilt) buildWalls()
      walls.visible = m === 'walls'
      applyRelief()
    },
    setWallHeight(h) {
      wallH = Math.max(0.001, h)
      walls.scale.y = wallH
    },
    setRelief(depth) {
      relief = depth
      applyRelief()
    },
    setMinStroke(px) {
      if (px === minStroke) return
      minStroke = px
      if (wallsBuilt) buildWalls()
    },
    dispose() {
      group.removeFromParent()
      clearWalls()
      for (const x of disposables) x.dispose()
    },
  }
  model.setWallHeight(wallH)
  model.setMode('relief')
  return model
}

/** Concatenate indexed box geometries (same attributes) into one. */
function mergeBoxes(geos: THREE.BufferGeometry[]): THREE.BufferGeometry {
  let vCount = 0
  let iCount = 0
  for (const g of geos) {
    vCount += g.getAttribute('position').count
    iCount += g.getIndex()!.count
  }
  const position = new Float32Array(vCount * 3)
  const normal = new Float32Array(vCount * 3)
  const index = vCount > 65535 ? new Uint32Array(iCount) : new Uint16Array(iCount)
  let vo = 0
  let io = 0
  for (const g of geos) {
    const p = g.getAttribute('position').array as Float32Array
    const n = g.getAttribute('normal').array as Float32Array
    const idx = g.getIndex()!.array
    position.set(p, vo * 3)
    normal.set(n, vo * 3)
    for (let k = 0; k < idx.length; k++) index[io + k] = idx[k] + vo
    vo += p.length / 3
    io += idx.length
  }
  const out = new THREE.BufferGeometry()
  out.setAttribute('position', new THREE.BufferAttribute(position, 3))
  out.setAttribute('normal', new THREE.BufferAttribute(normal, 3))
  out.setIndex(new THREE.BufferAttribute(index, 1))
  return out
}

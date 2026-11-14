import * as THREE from 'three'
import { CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js'
import type { Box, Drawing, RoomPoly } from '@/api/contract'
import { shapeStats, type SvgPlan } from './svgPlan'

/** Heights in metres. Swiss Dwellings and the IFC storeys carry no heights in the plan, so these are typical values. */
export const DEFAULT_WALL_H = 2.6
const SILL_H = 0.9
const HEAD_H = 2.1
const RAIL_H = 1.05
const FIXTURE_H = 0.45
const DOOR_LEAF_H = 2.05

export const COLORS = {
  wallTop: '#2f343c',
  wallSide: '#f3f1ec',
  floor: ['#e4cfae', '#c9d9e3', '#d9cfe6', '#cfe0c8'],
  outdoor: '#cfd2cc',
  glass: '#9cc8ee',
  fixture: '#ffffff',
  line: '#4b5563',
  evidence: '#166ed1',
  evidenceFloor: '#8fbcf0',
  selected: '#0f766e',
  base: '#fbfbfa',
}

export interface RoomHandle {
  room: RoomPoly
  mesh: THREE.Mesh<THREE.ShapeGeometry, THREE.MeshStandardMaterial>
  outline: THREE.LineLoop
  label: CSS2DObject
  baseColor: THREE.Color
  /** Centre of the label anchor in world coordinates. */
  anchor: THREE.Vector3
}

export interface PlanModel {
  group: THREE.Group
  /** Footprint size in metres (x, z) and the radius used to frame the camera. */
  size: { w: number; d: number; radius: number }
  /** Centre of the drawn content relative to the centre of the drawing frame, in metres (x, 0, z). */
  contentCenter: THREE.Vector3
  rooms: RoomHandle[]
  setWallHeight(h: number): void
  setHighlight(state: { evidence: Set<string>; hovered: string | null; selected: string | null }): void
  setPins(boxes: Box[]): void
  setLabels(on: boolean): void
  /** Hide labels that would overlap a more important one (evidence, selected, then larger rooms). */
  afterRender(camera: THREE.Camera, viewW: number, viewH: number): void
  /** Pct (0–100 of the plan image) to world (x, z). */
  toWorld(xPct: number, yPct: number): THREE.Vector2
  dispose(): void
}

interface Frame {
  /** metres per SVG px */
  sx: number
  sz: number
  cx: number
  cz: number
  W: number
  H: number
}

function frameOf(drawing: Drawing, W: number, H: number): Frame {
  const mx = drawing.m_per_pct_x > 0 ? drawing.m_per_pct_x : W / 4000
  const my = drawing.m_per_pct_y > 0 ? drawing.m_per_pct_y : H / 4000
  const sx = (mx * 100) / W
  const sz = (my * 100) / H
  return { sx, sz, cx: (W * sx) / 2, cz: (H * sz) / 2, W, H }
}

/** Matrix that turns a geometry built in SVG px (x right, y down, extrusion +z) into world metres (y up). */
function pxToWorld(f: Frame): THREE.Matrix4 {
  const m = new THREE.Matrix4().makeTranslation(-f.cx, 0, -f.cz)
  // rotateX(+90°) maps extrusion +z to -y and svg y to +z; the scale flips extrusion back to +y.
  m.multiply(new THREE.Matrix4().makeScale(f.sx, -1, f.sz))
  m.multiply(new THREE.Matrix4().makeRotationX(Math.PI / 2))
  return m
}

function extrude(shapes: THREE.Shape[], f: Frame): THREE.BufferGeometry | null {
  if (shapes.length === 0) return null
  const g = new THREE.ExtrudeGeometry(shapes, { depth: 1, bevelEnabled: false, curveSegments: 6 })
  g.applyMatrix4(pxToWorld(f))
  // The flip above inverts winding: put the faces back so lighting and culling stay right.
  flipWinding(g)
  g.computeVertexNormals()
  return g
}

function flipWinding(g: THREE.BufferGeometry) {
  const idx = g.getIndex()
  if (idx) {
    const a = idx.array as Uint16Array | Uint32Array
    for (let i = 0; i < a.length; i += 3) [a[i + 1], a[i + 2]] = [a[i + 2], a[i + 1]]
    idx.needsUpdate = true
    return
  }
  for (const name of Object.keys(g.attributes)) {
    const attr = g.getAttribute(name) as THREE.BufferAttribute
    const n = attr.itemSize
    const arr = attr.array as Float32Array
    for (let i = 0; i < attr.count; i += 3) {
      for (let k = 0; k < n; k++) {
        const b = (i + 1) * n + k
        const c = (i + 2) * n + k
        ;[arr[b], arr[c]] = [arr[c], arr[b]]
      }
    }
    attr.needsUpdate = true
  }
}

function pointInPoly(x: number, y: number, pts: THREE.Vector2[]): boolean {
  let inside = false
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const a = pts[i]
    const b = pts[j]
    if (a.y > y !== b.y > y && x < ((b.x - a.x) * (y - a.y)) / (b.y - a.y) + a.x) inside = !inside
  }
  return inside
}

function distToPolyEdges(x: number, y: number, pts: THREE.Vector2[]): number {
  let best = Infinity
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const a = pts[j]
    const b = pts[i]
    const dx = b.x - a.x
    const dy = b.y - a.y
    const L = dx * dx + dy * dy
    const t = L > 0 ? Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / L)) : 0
    best = Math.min(best, Math.hypot(x - (a.x + t * dx), y - (a.y + t * dy)))
  }
  return best
}

/**
 * A thin wall polygon that runs along outdoor space only (terrace or balcony edge) is a parapet or railing,
 * not a storey-high wall. Interior partitions are as thin but sit next to indoor rooms.
 */
function isRailing(shape: THREE.Shape, f: Frame, indoor: THREE.Vector2[][]): boolean {
  const { area, perimeter } = shapeStats(shape)
  const thickM = perimeter > 0 ? ((2 * area) / perimeter) * f.sx : 1
  if (thickM < 0.06) return true
  if (thickM > 0.15) return false
  const pts = shape.getSpacedPoints(24)
  const near = 0.35 / f.sx
  let hits = 0
  for (const p of pts) if (indoor.some((poly) => pointInPoly(p.x, p.y, poly) || distToPolyEdges(p.x, p.y, poly) < near)) hits++
  return hits / pts.length < 0.3
}

function labelEl(room: RoomPoly, fmt: Intl.NumberFormat): HTMLDivElement {
  const el = document.createElement('div')
  el.className = 'v3d-label'
  const name = document.createElement('span')
  name.className = 'v3d-label-name'
  name.textContent = room.name || room.num
  el.appendChild(name)
  if (room.area_m2 != null) {
    const a = document.createElement('span')
    a.className = 'v3d-label-area'
    a.textContent = `${fmt.format(room.area_m2)} m²`
    el.appendChild(a)
  }
  return el
}

export function buildPlanModel(drawing: Drawing, plan: SvgPlan): PlanModel {
  const f = frameOf(drawing, plan.width, plan.height)
  const toPx = (p: [number, number]) => new THREE.Vector2((p[0] / 100) * f.W, (p[1] / 100) * f.H)
  const toWorld = (xPct: number, yPct: number) =>
    new THREE.Vector2((xPct / 100) * f.W * f.sx - f.cx, (yPct / 100) * f.H * f.sz - f.cz)

  const group = new THREE.Group()
  const disposables: { dispose(): void }[] = []
  const track = <T extends { dispose(): void }>(x: T) => (disposables.push(x), x)

  // ---- rooms (floors, outlines, labels) ----
  const unitIds = [...new Set(drawing.rooms.map((r) => r.unit ?? ''))]
  const rooms: RoomHandle[] = []
  const indoorPolys: THREE.Vector2[][] = []
  const areaFmt = new Intl.NumberFormat(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })
  for (const room of drawing.rooms) {
    if (room.points.length < 3) continue
    const pts = room.points.map(toPx)
    if (!room.outdoor) indoorPolys.push(pts)
    const shape = new THREE.Shape(pts)
    const geo = track(new THREE.ShapeGeometry(shape))
    geo.applyMatrix4(new THREE.Matrix4().makeTranslation(-f.cx, 0, -f.cz).multiply(new THREE.Matrix4().makeScale(f.sx, 1, f.sz)).multiply(new THREE.Matrix4().makeRotationX(Math.PI / 2)))
    flipWinding(geo)
    geo.computeVertexNormals()
    const baseColor = new THREE.Color(
      room.outdoor ? COLORS.outdoor : COLORS.floor[Math.max(0, unitIds.indexOf(room.unit ?? '')) % COLORS.floor.length],
    )
    const mat = track(new THREE.MeshStandardMaterial({ color: baseColor.clone(), roughness: 0.85 }))
    const mesh = new THREE.Mesh(geo, mat)
    mesh.position.y = room.outdoor ? 0.004 : 0.008
    mesh.receiveShadow = true
    mesh.userData.roomId = room.room_id
    group.add(mesh)

    const linePts = room.points.map((p) => {
      const v = toWorld(p[0], p[1])
      return new THREE.Vector3(v.x, 0.03, v.y)
    })
    const lineGeo = track(new THREE.BufferGeometry().setFromPoints(linePts))
    const lineMat = track(new THREE.LineBasicMaterial({ color: COLORS.selected, transparent: true, opacity: 0 }))
    lineMat.userData.v3dDynamic = true // its opacity is driven by setHighlight, not by a building fade
    const outline = new THREE.LineLoop(lineGeo, lineMat)
    group.add(outline)

    const la = toWorld(room.label_at[0], room.label_at[1])
    const anchor = new THREE.Vector3(la.x, 0.15, la.y)
    const label = new CSS2DObject(labelEl(room, areaFmt))
    label.position.copy(anchor)
    group.add(label)
    rooms.push({ room, mesh, outline, label, baseColor, anchor })
  }

  // ---- walls ----
  const wallTop = track(new THREE.MeshStandardMaterial({ color: COLORS.wallTop, roughness: 0.7 }))
  const wallSide = track(new THREE.MeshStandardMaterial({ color: COLORS.wallSide, roughness: 0.9 }))
  const fullWalls: THREE.Shape[] = []
  const rails: THREE.Shape[] = []
  for (const s of plan.walls) (isRailing(s, f, indoorPolys) ? rails : fullWalls).push(s)
  const unitMeshes: { mesh: THREE.Mesh; y0: number; y1: (h: number) => number }[] = []
  const addUnit = (geo: THREE.BufferGeometry | null, mat: THREE.Material | THREE.Material[], y0: number, y1: (h: number) => number, shadow = true) => {
    if (!geo) return
    track(geo)
    const mesh = new THREE.Mesh(geo, mat)
    mesh.castShadow = shadow
    mesh.receiveShadow = true
    group.add(mesh)
    unitMeshes.push({ mesh, y0, y1 })
  }
  // ExtrudeGeometry groups: 0 = caps (top/bottom), 1 = sides.
  addUnit(extrude(fullWalls, f), [wallTop, wallSide], 0, (h) => h)
  addUnit(extrude(rails, f), [wallTop, wallSide], 0, (h) => Math.min(h, RAIL_H))

  // ---- windows: sill, glass, lintel ----
  const glassMat = track(
    new THREE.MeshStandardMaterial({ color: COLORS.glass, transparent: true, opacity: 0.35, roughness: 0.1, metalness: 0.1, depthWrite: false }),
  )
  const winGeo = extrude(plan.windows, f)
  if (winGeo) {
    addUnit(winGeo, [wallTop, wallSide], 0, (h) => Math.min(h, SILL_H))
    addUnit(winGeo.clone(), glassMat, SILL_H, (h) => Math.min(h, HEAD_H), false)
    addUnit(winGeo.clone(), [wallTop, wallSide], HEAD_H, (h) => h)
  }

  // ---- doors: lintels over frames, standing leaves, swing arcs on the floor ----
  const frames: THREE.Shape[] = []
  const floorSegs: number[] = []
  const leafGeos: THREE.BufferGeometry[] = []
  const pushSegs = (pts: THREE.Vector2[], y: number, out: number[]) => {
    for (let i = 0; i + 1 < pts.length; i++) {
      const a = toWorld((pts[i].x / f.W) * 100, (pts[i].y / f.H) * 100)
      const b = toWorld((pts[i + 1].x / f.W) * 100, (pts[i + 1].y / f.H) * 100)
      out.push(a.x, y, a.y, b.x, y, b.y)
    }
  }
  for (const pts of plan.doorLines) {
    if (pts.length < 2) continue
    const closed = pts.length > 3 && pts[0].distanceTo(pts[pts.length - 1]) < 0.75
    if (closed) {
      const xs = pts.map((p) => p.x)
      const ys = pts.map((p) => p.y)
      const minDim = Math.min((Math.max(...xs) - Math.min(...xs)) * f.sx, (Math.max(...ys) - Math.min(...ys)) * f.sz)
      if (minDim <= 0.5) frames.push(new THREE.Shape(pts))
      else pushSegs(pts, 0.02, floorSegs)
      continue
    }
    const len = pts[0].distanceTo(pts[pts.length - 1]) * f.sx
    if (pts.length === 2 && len > 0.55 && len < 1.4) {
      const a = toWorld((pts[0].x / f.W) * 100, (pts[0].y / f.H) * 100)
      const b = toWorld((pts[1].x / f.W) * 100, (pts[1].y / f.H) * 100)
      const leaf = new THREE.BoxGeometry(a.distanceTo(b), DOOR_LEAF_H, 0.04)
      leaf.rotateY(-Math.atan2(b.y - a.y, b.x - a.x))
      leaf.translate((a.x + b.x) / 2, DOOR_LEAF_H / 2, (a.y + b.y) / 2)
      leafGeos.push(leaf)
    } else pushSegs(pts, 0.02, floorSegs)
  }
  addUnit(extrude(frames, f), [wallTop, wallSide], HEAD_H, (h) => h)
  const leafMat = track(new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 0.6 }))
  const leaves = new THREE.Group()
  for (const g of leafGeos) {
    track(g)
    const m = new THREE.Mesh(g, leafMat)
    m.castShadow = true
    leaves.add(m)
  }
  group.add(leaves)

  // ---- fixtures: solid blocks for the outermost closed shapes, outlines on top ----
  const fixStats = plan.fixtureShapes.map((s) => ({ s, pts: s.getPoints(), area: shapeStats(s).area }))
  fixStats.sort((a, b) => b.area - a.area)
  const outer: typeof fixStats = []
  for (const fs of fixStats) {
    if (fs.area * f.sx * f.sz < 0.03) continue
    const c = fs.pts.reduce((acc, p) => acc.add(p), new THREE.Vector2()).divideScalar(fs.pts.length)
    if (outer.some((o) => pointInPoly(c.x, c.y, o.pts))) continue
    outer.push(fs)
  }
  const fixtureMat = track(new THREE.MeshStandardMaterial({ color: COLORS.fixture, roughness: 0.35 }))
  const fixGeo = extrude(outer.map((o) => o.s), f)
  if (fixGeo) {
    track(fixGeo)
    fixGeo.scale(1, FIXTURE_H, 1)
    const m = new THREE.Mesh(fixGeo, fixtureMat)
    m.castShadow = true
    m.receiveShadow = true
    group.add(m)
  }
  const fixSegs: number[] = []
  for (const pts of plan.fixtureLines) pushSegs(pts, FIXTURE_H + 0.004, fixSegs)
  const lineMat = track(new THREE.LineBasicMaterial({ color: COLORS.line, transparent: true, opacity: 0.55 }))
  for (const segs of [floorSegs, fixSegs]) {
    if (!segs.length) continue
    const g = track(new THREE.BufferGeometry())
    g.setAttribute('position', new THREE.Float32BufferAttribute(segs, 3))
    group.add(new THREE.LineSegments(g, lineMat))
  }

  // ---- evidence pins ----
  const pins = new THREE.Group()
  group.add(pins)
  const pinMat = track(new THREE.MeshBasicMaterial({ color: COLORS.evidence }))
  const ringMat = track(new THREE.MeshBasicMaterial({ color: COLORS.evidence, transparent: true, opacity: 0.35, side: THREE.DoubleSide, depthWrite: false }))
  const stemGeo = track(new THREE.CylinderGeometry(0.025, 0.025, 1, 8))
  const ringGeo = track(new THREE.RingGeometry(0.22, 0.34, 32).rotateX(-Math.PI / 2))
  let wallH = DEFAULT_WALL_H
  let pinKey = ''
  const labelSize = new WeakMap<HTMLElement, [number, number]>()

  const clearPins = () => {
    for (const c of [...pins.children]) {
      c.traverse((o) => {
        if (o instanceof CSS2DObject) {
          o.removeFromParent()
          o.element.remove()
        }
      })
      pins.remove(c)
    }
  }

  // Centre the drawn content (the SVG frame has wide margins) and put a base board under it,
  // like an architect's presentation model.
  const bb = new THREE.Box3().setFromObject(group)
  const center = bb.getCenter(new THREE.Vector3())
  const w = bb.max.x - bb.min.x
  const d = bb.max.z - bb.min.z
  group.position.set(-center.x, 0, -center.z)
  const baseGeo = track(new THREE.BoxGeometry(w + 1.6, 0.16, d + 1.6))
  const baseMat = track(new THREE.MeshStandardMaterial({ color: COLORS.base, roughness: 0.95 }))
  const base = new THREE.Mesh(baseGeo, baseMat)
  base.position.set(center.x, -0.081, center.z)
  base.receiveShadow = true
  group.add(base)
  const root = new THREE.Group()
  root.name = 'plan'
  root.add(group)

  const model: PlanModel = {
    group: root,
    size: { w, d, radius: Math.hypot(w, d) / 2 },
    contentCenter: new THREE.Vector3(center.x, 0, center.z),
    rooms,
    toWorld,
    setWallHeight(h) {
      wallH = Math.max(0.001, h)
      for (const u of unitMeshes) {
        const top = u.y1(wallH)
        const span = top - u.y0
        u.mesh.visible = span > 0.005
        u.mesh.position.y = u.y0
        u.mesh.scale.y = Math.max(0.001, span)
      }
      leaves.visible = wallH > 0.3
      leaves.scale.y = Math.min(1, wallH / DOOR_LEAF_H)
      for (const p of pins.children) p.userData.place?.(wallH)
    },
    setHighlight({ evidence, hovered, selected }) {
      for (const r of rooms) {
        const id = r.room.room_id
        const c = r.baseColor.clone()
        if (evidence.has(id)) c.set(COLORS.evidenceFloor)
        if (hovered === id) c.lerp(new THREE.Color('#ffffff'), 0.45)
        r.mesh.material.color.copy(c)
        r.mesh.material.emissive.set(evidence.has(id) ? COLORS.evidence : '#000000')
        r.mesh.material.emissiveIntensity = evidence.has(id) ? 0.18 : 0
        const lm = r.outline.material as THREE.LineBasicMaterial
        lm.opacity = selected === id ? 1 : evidence.has(id) ? 0.6 : 0
        lm.color.set(selected === id ? COLORS.selected : COLORS.evidence)
        r.label.element.classList.toggle('is-evidence', evidence.has(id))
        r.label.element.classList.toggle('is-selected', selected === id)
      }
    },
    setPins(boxes) {
      const key = boxes.map((b) => b.id).join('|')
      if (key === pinKey) return
      pinKey = key
      clearPins()
      boxes.forEach((b, i) => {
        const c = toWorld(b.x + b.w / 2, b.y + b.h / 2)
        const pin = new THREE.Group()
        pin.position.set(c.x, 0, c.y)
        const ring = new THREE.Mesh(ringGeo, ringMat)
        ring.position.y = 0.035
        const stem = new THREE.Mesh(stemGeo, pinMat)
        const el = document.createElement('div')
        el.className = 'v3d-pin'
        el.textContent = String(i + 1)
        el.title = b.id
        const badge = new CSS2DObject(el)
        pin.add(ring, stem, badge)
        pin.userData.place = (h: number) => {
          const top = Math.max(h, 1) + 0.5
          stem.scale.y = top
          stem.position.y = top / 2
          badge.position.y = top + 0.15
        }
        pin.userData.place(wallH)
        pins.add(pin)
      })
    },
    setLabels(on) {
      for (const r of rooms) r.label.visible = on
    },
    afterRender(camera, viewW, viewH) {
      const placed: [number, number, number, number][] = []
      const v = new THREE.Vector3()
      // Evidence pins always win: room labels give way to them.
      for (const pin of pins.children)
        for (const c of pin.children)
          if (c instanceof CSS2DObject) {
            c.getWorldPosition(v).project(camera)
            placed.push([((v.x + 1) / 2) * viewW - 14, ((1 - v.y) / 2) * viewH - 14, 28, 28])
          }
      const prio = (r: RoomHandle) => {
        const cl = r.label.element.classList
        return (cl.contains('is-selected') ? 2e6 : 0) + (cl.contains('is-evidence') ? 1e6 : 0) + (r.room.area_m2 ?? 0)
      }
      for (const r of [...rooms].sort((a, b) => prio(b) - prio(a))) {
        if (!r.label.visible) continue
        const el = r.label.element
        // Measured once (labels do not change size); reading layout every frame would force reflows.
        let size = labelSize.get(el)
        if (!size || !size[0]) {
          size = [el.offsetWidth, el.offsetHeight]
          labelSize.set(el, size)
        }
        const lw = size[0] || 90
        const lh = size[1] || 30
        r.label.getWorldPosition(v).project(camera)
        const x = ((v.x + 1) / 2) * viewW - lw / 2
        const y = ((1 - v.y) / 2) * viewH - lh / 2
        const hit = placed.some(([px, py, pw, ph]) => x < px + pw + 2 && x + lw + 2 > px && y < py + ph + 2 && y + lh + 2 > py)
        el.classList.toggle('is-culled', hit)
        if (!hit) placed.push([x, y, lw, lh])
      }
    },
    dispose() {
      clearPins()
      // CSS2D elements are only taken out of the DOM when their own object is removed.
      for (const r of rooms) {
        r.label.removeFromParent()
        r.label.element.remove()
      }
      root.removeFromParent()
      for (const x of disposables) x.dispose()
    },
  }
  model.setWallHeight(DEFAULT_WALL_H)
  return model
}

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { bundledDrawing } from '@/data/drawing'
import type { Box } from '@/api/contract'
import { isRebuiltPlanSvg, parsePlanSvg } from './svgPlan'
import { buildPlanModel, initialWallHeight } from './planModel'
import { inkMask, keepWallLike, openMask, wallRects } from './imageModel'

const svgText = readFileSync('public/drawings/swiss-46639/plan.svg', 'utf8')
// Counted independently in the SVG text: one <path> per wall / window, by the renderer's fixed styles.
const count = (re: RegExp) => (svgText.match(re) ?? []).length
const WALLS = count(/fill="#2f343c" stroke="#2f343c"/g)
const WINDOWS = count(/fill="#ffffff" stroke="#374151"/g)

describe('plan.svg layers', () => {
  it('recognises a rebuilt plan and nothing else', () => {
    expect(isRebuiltPlanSvg(svgText)).toBe(true)
    expect(isRebuiltPlanSvg('<svg><rect fill="#000"/></svg>')).toBe(false)
  })
  it('reads every wall and window polygon', () => {
    const p = parsePlanSvg(svgText)
    expect([p.width, p.height]).toEqual(bundledDrawing.frame.px)
    expect(WALLS).toBeGreaterThan(0)
    // One wall path is one simple polygon in this renderer, so shapes == paths.
    expect(p.walls).toHaveLength(WALLS)
    expect(p.windows).toHaveLength(WINDOWS)
    expect(p.doorLines.length).toBeGreaterThan(0)
  })
})

describe('plan model', () => {
  const plan = parsePlanSvg(svgText)

  it('has one floor per room with an outline, centred on the drawn content', () => {
    const m = buildPlanModel(bundledDrawing, plan)
    expect(m.rooms).toHaveLength(bundledDrawing.rooms.filter((r) => r.points.length >= 3).length)
    const bb = new THREE.Box3().setFromObject(m.group)
    expect(Math.abs((bb.min.x + bb.max.x) / 2)).toBeLessThan(0.05)
    expect(Math.abs((bb.min.z + bb.max.z) / 2)).toBeLessThan(0.05)
    // Frame is 1221 px at 40 px/m: the content cannot be wider than that (plus the 0.8 m board lip).
    expect(bb.max.x - bb.min.x).toBeLessThanOrEqual(1221 / 40 + 1.6 + 1e-6)
    m.dispose()
  })

  it('puts each room label inside its own floor', () => {
    const m = buildPlanModel(bundledDrawing, plan)
    m.group.updateMatrixWorld(true)
    for (const r of m.rooms) {
      const bb = new THREE.Box3().setFromObject(r.mesh)
      const p = r.label.getWorldPosition(new THREE.Vector3())
      expect(p.x).toBeGreaterThanOrEqual(bb.min.x - 1e-6)
      expect(p.x).toBeLessThanOrEqual(bb.max.x + 1e-6)
      expect(p.z).toBeGreaterThanOrEqual(bb.min.z - 1e-6)
      expect(p.z).toBeLessThanOrEqual(bb.max.z + 1e-6)
    }
    m.dispose()
  })

  it('starts a plan with an estimated scale with walls in proportion to its footprint', () => {
    const estimated = { ...bundledDrawing, rooms: bundledDrawing.rooms.map((r) => ({ ...r, area_source: 'estimate' as const })) }
    expect(initialWallHeight(bundledDrawing, { w: 8, d: 7 })).toBe(2.6) // a stated scale: the typical storey
    expect(initialWallHeight(estimated, { w: 8, d: 7 })).toBe(1.4)
    expect(initialWallHeight(estimated, { w: 60, d: 40 })).toBe(2.6)
    expect(initialWallHeight(estimated, { w: 3, d: 2 })).toBe(1.2)
  })
  it('frames the walls as well as the footprint', () => {
    const m = buildPlanModel(bundledDrawing, plan)
    expect(m.size.radius).toBeCloseTo(Math.hypot(m.size.w, m.size.d) / 2 + 2.6)
    m.dispose()
  })
  it('cuts walls down with the wall height and keeps railings at railing height', () => {
    const m = buildPlanModel(bundledDrawing, plan)
    const heights = () => {
      const hs: number[] = []
      m.group.traverse((o) => {
        if ((o as THREE.Mesh).isMesh && o.visible && o.position.y === 0 && o.scale.y !== 1) hs.push(+o.scale.y.toFixed(3))
      })
      return hs
    }
    m.setWallHeight(2.6)
    expect(heights()).toContain(2.6) // walls
    expect(heights()).toContain(1.05) // terrace railings (0.04 m thick walls along outdoor rooms)
    expect(heights()).toContain(0.9) // window sills
    m.setWallHeight(0.5)
    expect(Math.max(...heights())).toBeCloseTo(0.5)
    m.dispose()
  })

  it('places one numbered pin per box and cleans its DOM up', () => {
    const m = buildPlanModel(bundledDrawing, plan)
    const boxes: Box[] = bundledDrawing.evidence.window.slice(0, 3)
    m.setPins(boxes)
    const pins: string[] = []
    m.group.traverse((o) => {
      if (o.type === 'Object3D' && (o as unknown as { element?: HTMLElement }).element?.className === 'v3d-pin')
        pins.push((o as unknown as { element: HTMLElement }).element.textContent ?? '')
    })
    expect(pins).toEqual(['1', '2', '3'])
    m.setPins([])
    let left = 0
    m.group.traverse((o) => {
      if ((o as unknown as { element?: HTMLElement }).element?.className === 'v3d-pin') left++
    })
    expect(left).toBe(0)
    m.dispose()
  })
})

describe('image pixels', () => {
  const W = 40
  const H = 30
  const blank = () => new Uint8Array(W * H)
  const rect = (m: Uint8Array, x: number, y: number, w: number, h: number) => {
    for (let yy = y; yy < y + h; yy++) for (let xx = x; xx < x + w; xx++) m[yy * W + xx] = 1
  }
  const sum = (m: Uint8Array) => m.reduce((a, b) => a + b, 0)

  it('opening keeps a 5 px bar and drops a 1 px line', () => {
    const m = blank()
    rect(m, 2, 2, 30, 5) // 150 px bar
    rect(m, 2, 20, 30, 1) // hairline
    const o = openMask(m, W, H, 2)
    expect(sum(o)).toBe(150)
    expect(o[20 * W + 10]).toBe(0)
  })

  it('greedy rectangles cover the mask exactly once', () => {
    const m = blank()
    rect(m, 1, 1, 10, 3)
    rect(m, 1, 4, 3, 10) // an L
    rect(m, 20, 5, 4, 4)
    const rects = wallRects(m, W, H)
    const cover = new Uint8Array(W * H)
    for (const [x, y, w, h] of rects)
      for (let yy = y; yy < y + h; yy++)
        for (let xx = x; xx < x + w; xx++) {
          expect(cover[yy * W + xx]).toBe(0)
          cover[yy * W + xx] = 1
        }
    expect([...cover]).toEqual([...m])
  })

  it('keeps long thin runs and drops compact blobs', () => {
    const m = blank()
    rect(m, 1, 1, 36, 3) // wall run: 36 × 3, elongation 12
    rect(m, 10, 12, 12, 12) // filled square blob
    const k = keepWallLike(m, W, H, 2)
    expect(sum(k)).toBe(36 * 3)
  })

  it('finds light ink on dark paper and dark ink on light paper', () => {
    const w = 60
    const h = 60
    const make = (paper: number, ink: number) => {
      const data = new Uint8ClampedArray(w * h * 4)
      for (let i = 0; i < w * h; i++) {
        const x = i % w
        const v = x >= 28 && x < 31 ? ink : paper
        data.set([v, v, v, 255], i * 4)
      }
      return { w, h, data }
    }
    const blue = inkMask(make(50, 230))
    expect(blue.darkPaper).toBe(true)
    expect(blue.ink[30 * w + 29]).toBeGreaterThan(0.9)
    expect(blue.ink[30 * w + 5]).toBe(0)
    const paper = inkMask(make(240, 20))
    expect(paper.darkPaper).toBe(false)
    expect(paper.ink[30 * w + 29]).toBeGreaterThan(0.9)
    expect(paper.ink[30 * w + 5]).toBe(0)
  })
})

describe('base board', () => {
  it('follows an L-shaped footprint instead of its bounding rectangle', async () => {
    const { footprintStrips } = await import('./footprint')
    const L = [
      { x: 0, y: 0 }, { x: 20, y: 0 }, { x: 20, y: 6 }, { x: 6, y: 6 }, { x: 6, y: 20 }, { x: 0, y: 20 },
    ]
    const strips = footprintStrips([L], 0.8)
    const covers = (x: number, z: number) => strips.some((s) => x >= s.x0 && x <= s.x1 && z >= s.z0 && z <= s.z1)
    expect(covers(3, 3) && covers(18, 3) && covers(3, 18)).toBe(true)
    expect(covers(-0.5, -0.5) && covers(20.6, 3)).toBe(true) // the lip
    expect(covers(14, 14) || covers(8, 8)).toBe(false) // the empty corner of the L
    const area = strips.reduce((a, s) => a + (s.x1 - s.x0) * (s.z1 - s.z0), 0)
    expect(area).toBeGreaterThan(204)
    expect(area).toBeLessThan(21.6 * 21.6 * 0.75)
    expect(Math.min(...strips.map((s) => s.x0))).toBeCloseTo(-0.8, 5)
    expect(Math.max(...strips.map((s) => s.x1))).toBeCloseTo(20.8, 5)
    // a courtyard is part of the board; nothing to stand on gives no board
    const ring = footprintStrips([[{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 1 }, { x: 0, y: 1 }], [{ x: 0, y: 9 }, { x: 10, y: 9 }, { x: 10, y: 10 }, { x: 0, y: 10 }],
      [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 1, y: 10 }, { x: 0, y: 10 }], [{ x: 9, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 9, y: 10 }]], 0.5)
    expect(ring.some((s) => 5 >= s.x0 && 5 <= s.x1 && 5 >= s.z0 && 5 <= s.z1)).toBe(true)
    expect(footprintStrips([], 0.8)).toEqual([])
  })

  it('the plan model stands on a board named base that covers its rooms', () => {
    const m = buildPlanModel(bundledDrawing, parsePlanSvg(svgText))
    const base = m.group.getObjectByName('base') as THREE.Mesh
    expect(base).toBeTruthy()
    base.geometry.computeBoundingBox()
    const bb = new THREE.Box3().setFromObject(m.rooms[0].mesh.parent!)
    expect(base.geometry.boundingBox!.max.y).toBeLessThan(0)
    expect(bb.isEmpty()).toBe(false)
    m.dispose()
  })
})

describe('has3dModel', () => {
  it('is offered for a vector plan (scan with a wall model, CAD bundle, IFC storey), not for a page image', async () => {
    const { has3dModel } = await import('./available')
    const img = (rebuilt: string) => ({ rebuilt })
    // a PDF sheet shown as its page render, a scan without rooms or walls
    expect(has3dModel({ kind: 'raster', images: img('/drawings/st-x/original.png?v=1') })).toBe(false)
    // a scan with a wall model: the plan drawn from it
    expect(has3dModel({ kind: 'raster', images: img('/drawings/st-x/plan.svg?v=1') })).toBe(true)
    expect(has3dModel({ kind: 'cad_dxf', images: img('/drawings/swiss-46639/plan.svg') })).toBe(true)
    expect(has3dModel({ kind: 'ifc', images: img('/drawings/st-b--eg/plan.svg') })).toBe(true)
  })
})

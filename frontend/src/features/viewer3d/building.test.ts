import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import type { Drawing } from '@/api/contract'
import { bundledDrawing } from '@/data/drawing'
import { buildBuilding, STACK_WALL_H, type FloorInput } from './buildingModel'
import type { PlanModel } from './planModel'
import { Spring, springStep } from './spring'

/** A stand-in floor: a box footprint and the PlanModel calls the building makes, recorded. */
function fakeFloor(id: string, frameShift: [number, number], center = new THREE.Vector3()): FloorInput & { walls: number[] } {
  const group = new THREE.Group()
  group.add(new THREE.Mesh(new THREE.BoxGeometry(10, 0.2, 6), new THREE.MeshStandardMaterial()))
  const walls: number[] = []
  const model = {
    group,
    size: { w: 10, d: 6, radius: Math.hypot(10, 6) / 2 },
    contentCenter: center,
    rooms: [],
    setWallHeight: (h: number) => walls.push(h),
    setHighlight: () => {},
    setPins: () => {},
    setLabels: () => {},
    afterRender: () => {},
    toWorld: () => new THREE.Vector2(),
    dispose: () => {},
  } as unknown as PlanModel
  const drawing = {
    ...bundledDrawing,
    id,
    frame: { ...bundledDrawing.frame, dxf_min: [frameShift[0], frameShift[1]], dxf_max: [frameShift[0] + 10, frameShift[1] + 6], m_per_unit: 1 },
  } as Drawing
  return { id, label: id, drawing, model, walls }
}

const settle = (b: ReturnType<typeof buildBuilding>) => {
  for (let i = 0; i < 600; i++) b.tick!(1 / 60)
}
const worldY = (b: ReturnType<typeof buildBuilding>, i: number) => b.group.children[i].position.y
const opacity = (b: ReturnType<typeof buildBuilding>, i: number) => {
  let o = 1
  b.group.children[i].traverse((x) => {
    const m = (x as THREE.Mesh).material as THREE.Material | undefined
    if (m) o = m.opacity
  })
  return o
}

describe('building stack', () => {
  const opts = { onFloorClick: () => {}, onFloorHover: () => {}, reducedMotion: false }

  it('stacks floors bottom to top with equal spacing and keeps their walls low', () => {
    const floors = [fakeFloor('g', [0, 0]), fakeFloor('1', [0, 0]), fakeFloor('2', [0, 0])]
    const b = buildBuilding(floors, opts)
    b.setMode('stack', 'g')
    settle(b)
    const ys = [0, 1, 2].map((i) => worldY(b, i))
    expect(ys[0]).toBeCloseTo(0, 3)
    expect(ys[1]).toBeGreaterThan(0)
    expect(ys[2] - ys[1]).toBeCloseTo(ys[1] - ys[0], 3)
    for (const f of floors) expect(f.walls.at(-1)).toBeCloseTo(STACK_WALL_H, 2)
    b.dispose()
  })

  it('places floors by their real frame coordinates (a shifted storey stays shifted)', () => {
    // Second floor's frame sits 4 m east and 2 m north of the first: it must end up +4 x and -2 z (north is -z).
    const b = buildBuilding([fakeFloor('a', [0, 0]), fakeFloor('b', [4, 2])], opts)
    const pa = b.group.children[0].position
    const pb = b.group.children[1].position
    expect(pb.x - pa.x).toBeCloseTo(4, 6)
    expect(pb.z - pa.z).toBeCloseTo(-2, 6)
    b.dispose()
  })

  it('focus: the floor gets full walls, floors above lift away and vanish, floors below sink as ghosts', () => {
    const floors = [fakeFloor('g', [0, 0]), fakeFloor('1', [0, 0]), fakeFloor('2', [0, 0])]
    const b = buildBuilding(floors, opts)
    b.setMode('stack', 'g')
    settle(b)
    const rest = [0, 1, 2].map((i) => worldY(b, i))
    b.setWallHeight(2.6)
    b.setMode('floor', '1')
    settle(b)
    expect(floors[1].walls.at(-1)).toBeCloseTo(2.6, 2)
    expect(worldY(b, 1)).toBeCloseTo(rest[1], 3)
    expect(worldY(b, 2)).toBeGreaterThan(rest[2])
    expect(b.group.children[2].visible).toBe(false)
    expect(worldY(b, 0)).toBeLessThan(rest[0])
    expect(opacity(b, 0)).toBeGreaterThan(0.05)
    expect(opacity(b, 0)).toBeLessThan(0.3)
    // Back to the stack: everything returns.
    b.setMode('stack', '1')
    settle(b)
    expect(worldY(b, 2)).toBeCloseTo(rest[2], 3)
    expect(opacity(b, 0)).toBeCloseTo(1, 3)
    b.dispose()
  })

  it('frames the whole stack, or just the focused floor at its height', () => {
    const b = buildBuilding([fakeFloor('g', [0, 0]), fakeFloor('1', [0, 0])], opts)
    const all = b.framing('stack', 'g')
    const one = b.framing('floor', '1')
    expect(all.radius).toBeGreaterThan(one.radius)
    expect(one.center.y).toBeGreaterThan(0)
    b.dispose()
  })
})

describe('springs', () => {
  it('reach the target without overshoot (critically damped)', () => {
    let x = 0
    let v = 0
    let max = 0
    for (let i = 0; i < 300; i++) {
      ;[x, v] = springStep(x, v, 1, 1 / 60, 0.4)
      max = Math.max(max, x)
    }
    expect(x).toBeCloseTo(1, 4)
    expect(max).toBeLessThanOrEqual(1 + 1e-9)
  })
  it('keep their velocity when re-targeted mid-flight (no jump)', () => {
    const s = new Spring(0, 0.4)
    s.target = 1
    for (let i = 0; i < 10; i++) s.step(1 / 60)
    const x = s.x
    const v = s.v
    s.target = -1
    expect(s.x).toBe(x)
    expect(s.v).toBe(v)
    s.step(1 / 60)
    expect(Math.abs(s.x - x)).toBeLessThan(0.05)
  })
})

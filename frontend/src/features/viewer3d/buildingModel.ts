import * as THREE from 'three'
import { CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js'
import type { Box, Drawing } from '@/api/contract'
import type { Showable } from './engine'
import type { ImageModel } from './imageModel'
import type { PlanModel } from './planModel'
import { Spring } from './spring'

/**
 * Several floors of one building as one scene.
 *  - stack: every floor, exploded vertically like an architect's axonometric, walls kept low so each plan reads;
 *  - floor: one floor in focus with full-height walls; floors above lift up and away, floors below sink and
 *    stay as faint context (enter and leave along the same vertical path).
 * Every value (height, opacity, wall height) is a critically damped spring, so any change mid-motion re-targets
 * smoothly. Floors are placed by the drawing frames' real coordinates when every floor has one.
 */
export type StackMode = 'stack' | 'floor'

export interface FloorInput {
  id: string
  label: string
  drawing: Drawing
  model: PlanModel | ImageModel
}

export interface FloorEvidence {
  rooms: Set<string>
  boxes: Box[]
}

interface FloorHandle {
  id: string
  label: string
  index: number
  model: PlanModel | ImageModel
  root: THREE.Group
  y: Spring
  fade: Spring
  wall: Spring
  /** Seconds left before this floor starts moving (the assembling intro). */
  delay: number
  labelObj: CSS2DObject
  countEl: HTMLSpanElement
  materials: { m: THREE.Material; opacity: number; transparent: boolean; depthWrite: boolean }[]
  appliedFade: number
}

export interface BuildingModel extends Showable {
  floorIds: string[]
  mode: StackMode
  focusId: string
  setMode(mode: StackMode, focusId: string): void
  setHover(id: string | null): void
  /** Camera framing for a mode: the whole stack, or one floor. */
  framing(mode: StackMode, focusId: string): { center: THREE.Vector3; radius: number; dir?: THREE.Vector3 }
  /** Evidence, hovered and selected room per floor (rooms only light on plan floors). */
  setEvidence(
    per: Map<string, FloorEvidence>,
    hoveredRoom: string | null,
    selectedRoom: string | null,
    numberOf?: (b: Box) => number,
  ): void
  setCounts(counts: Map<string, number>): void
  setRoomLabels(on: boolean): void
  floorOf(o: THREE.Object3D): string | null
  dispose(): void
}

export const STACK_WALL_H = 1.0
const RESPONSE = 0.42 // Apple's "move" spring is 0.4 s; a touch slower reads better for large 3D moves.

const isPlan = (m: PlanModel | ImageModel): m is PlanModel => 'rooms' in m

/** Frame centre of a drawing in model metres (x right, y north), when the frame is meaningful. */
function frameCenter(d: Drawing): [number, number] | null {
  const f = d.frame
  if (!f || !Array.isArray(f.dxf_min) || !Array.isArray(f.dxf_max)) return null
  const k = f.m_per_unit > 0 ? f.m_per_unit : 1
  const cx = ((f.dxf_min[0] + f.dxf_max[0]) / 2) * k
  const cy = ((f.dxf_min[1] + f.dxf_max[1]) / 2) * k
  return Number.isFinite(cx) && Number.isFinite(cy) ? [cx, cy] : null
}

export function buildBuilding(
  inputs: FloorInput[],
  opts: { onFloorClick(id: string): void; onFloorHover(id: string | null): void; reducedMotion: boolean },
): BuildingModel {
  const group = new THREE.Group()
  group.name = 'building'
  const footR = Math.max(...inputs.map((f) => f.model.size.radius))
  // Generous spacing so each floor shows between its neighbours, like an exploded axonometric.
  const pitch = THREE.MathUtils.clamp(footR * 0.62, 3.6, 14)

  // Horizontal placement: real coordinates when every floor is a CAD/IFC plan with a frame, else centred.
  const offsets = inputs.map((f) => {
    const c = isPlan(f.model) ? frameCenter(f.drawing) : null
    return c && isPlan(f.model) ? new THREE.Vector2(c[0] + f.model.contentCenter.x, -c[1] + f.model.contentCenter.z) : null
  })
  const aligned = offsets.every(Boolean)
  const mean = new THREE.Vector2()
  if (aligned) {
    for (const o of offsets) mean.add(o!)
    mean.divideScalar(offsets.length)
  }

  const floors: FloorHandle[] = inputs.map((f, index) => {
    const root = new THREE.Group()
    root.add(f.model.group)
    if (aligned) root.position.set(offsets[index]!.x - mean.x, 0, offsets[index]!.y - mean.y)
    root.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) {
        o.userData.floorId = f.id
        o.userData.castsShadow = o.castShadow
      }
    })
    const materials: FloorHandle['materials'] = []
    const seen = new Set<THREE.Material>()
    root.traverse((o) => {
      const mat = (o as THREE.Mesh).material as THREE.Material | THREE.Material[] | undefined
      for (const m of Array.isArray(mat) ? mat : mat ? [mat] : []) {
        if (seen.has(m) || m.userData.v3dDynamic || m instanceof THREE.ShadowMaterial) continue
        seen.add(m)
        materials.push({ m, opacity: m.opacity, transparent: m.transparent, depthWrite: m.depthWrite })
      }
    })

    // Floor label: "L2  Level 2  (3)", sits off the slab's left corner like a wayfinding sign.
    const el = document.createElement('button')
    el.type = 'button'
    el.className = 'v3d-floor-label'
    el.dataset.floorLabel = f.id
    const chip = document.createElement('span')
    chip.className = 'v3d-floor-chip'
    chip.textContent = `L${index + 1}`
    const name = document.createElement('span')
    name.className = 'v3d-floor-name'
    name.textContent = f.label
    const countEl = document.createElement('span')
    countEl.className = 'v3d-floor-count'
    countEl.hidden = true
    el.append(chip, name, countEl)
    // The label lives in the CSS2D layer above the canvas: keep its clicks from also reaching the canvas.
    el.addEventListener('pointerdown', (e) => e.stopPropagation())
    el.addEventListener('pointerup', (e) => e.stopPropagation())
    el.addEventListener('click', (e) => {
      e.stopPropagation()
      opts.onFloorClick(f.id)
    })
    el.addEventListener('pointerenter', () => opts.onFloorHover(f.id))
    el.addEventListener('pointerleave', () => opts.onFloorHover(null))
    const labelObj = new CSS2DObject(el)
    const { w, d } = f.model.size
    labelObj.position.set(-w / 2 - 0.4, 0.2, d / 2 + 0.4)
    root.add(labelObj)
    group.add(root)

    const restY = index * pitch
    const start = opts.reducedMotion ? restY : restY - pitch * 1.5
    return {
      id: f.id,
      label: f.label,
      index,
      model: f.model,
      root,
      y: new Spring(start, RESPONSE),
      fade: new Spring(opts.reducedMotion ? 1 : 0, 0.32),
      wall: new Spring(STACK_WALL_H, 0.5),
      delay: opts.reducedMotion ? 0 : 0.12 + index * 0.08,
      labelObj,
      countEl,
      materials,
      appliedFade: -1,
    }
  })

  let mode: StackMode = 'stack'
  let focusId = floors[0].id
  let hover: string | null = null
  let fullWallH = 2.6
  let roomLabels = true

  const applyFade = (f: FloorHandle) => {
    const k = f.fade.x
    if (Math.abs(k - f.appliedFade) < 1e-5) return
    f.appliedFade = k
    for (const r of f.materials) {
      const solid = k > 0.995
      r.m.opacity = r.opacity * k
      r.m.transparent = r.transparent || !solid
      r.m.depthWrite = r.depthWrite && solid
    }
    f.root.visible = k > 0.01
    f.labelObj.visible = mode === 'stack' ? k > 0.3 : false
    f.root.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) o.castShadow = k > 0.6 && o.userData.castsShadow === true
    })
  }

  const retarget = () => {
    const fi = floors.findIndex((f) => f.id === focusId)
    for (const f of floors) {
      const rest = f.index * pitch
      if (mode === 'stack') {
        // Hover lifts the floor toward the pointer (a hint of "step in"); no dimming, which would turn the
        // other floors see-through.
        f.y.target = rest + (hover === f.id ? pitch * 0.14 : 0)
        f.fade.target = 1
        f.wall.target = Math.min(fullWallH, STACK_WALL_H)
      } else if (f.index === fi) {
        f.y.target = rest
        f.fade.target = 1
        f.wall.target = fullWallH
      } else if (f.index < fi) {
        f.y.target = rest - pitch * 0.35
        f.fade.target = 0.16
        f.wall.target = Math.min(fullWallH, STACK_WALL_H)
      } else {
        f.y.target = rest + pitch * 1.8
        f.fade.target = 0
        f.wall.target = Math.min(fullWallH, STACK_WALL_H)
      }
      if (opts.reducedMotion) {
        f.y.snap(f.y.target)
        f.fade.snap(f.fade.target)
        f.wall.snap(f.wall.target)
      }
      // Room labels only on the floor in focus; the stack is read through the floor labels.
      if (isPlan(f.model)) f.model.setLabels(roomLabels && mode === 'floor' && f.index === fi)
      // Floor labels are the stack's wayfinding; in focus the title pill says where you are.
      f.labelObj.visible = mode === 'stack' && f.fade.x > 0.3
      f.labelObj.element.classList.toggle('is-current', f.id === focusId)
      f.labelObj.element.classList.toggle('is-hover', f.id === hover)
    }
  }

  const stackHeight = (floors.length - 1) * pitch
  const radius = Math.hypot(footR, stackHeight / 2 + STACK_WALL_H)

  const model: BuildingModel = {
    group,
    size: { w: footR * 2, d: footR * 2, radius },
    floorIds: floors.map((f) => f.id),
    get mode() {
      return mode
    },
    get focusId() {
      return focusId
    },
    setMode(m, id) {
      mode = m
      if (floors.some((f) => f.id === id)) focusId = id
      retarget()
    },
    setHover(id) {
      if (hover === id) return
      hover = id
      retarget()
    },
    framing(m, id) {
      // The stack reads best a little wider than its bounding sphere (labels sit off the left corners).
      // Seen from lower down (about 30 degrees) so the gaps between floors open up; a little wider than the
      // bounding sphere because the floor labels sit off the left corners.
      if (m === 'stack')
        return { center: new THREE.Vector3(0, stackHeight / 2, 0), radius: radius * 1.18, dir: new THREE.Vector3(0.55, 0.6, 0.85) }
      const f = floors.find((x) => x.id === id) ?? floors[0]
      return { center: new THREE.Vector3(0, f.index * pitch, 0), radius: f.model.size.radius }
    },
    setWallHeight(h) {
      fullWallH = h
      retarget()
    },
    tick(dt) {
      let moving = false
      for (const f of floors) {
        if (f.delay > 0) {
          f.delay -= dt
          moving = true
          applyFade(f)
          f.root.position.y = f.y.x
          continue
        }
        if (f.y.step(dt)) moving = true
        if (f.fade.step(dt)) moving = true
        if (f.wall.step(dt)) moving = true
        f.root.position.y = f.y.x
        f.model.setWallHeight(Math.max(0.001, f.wall.x))
        applyFade(f)
      }
      return moving
    },
    afterRender(camera, w, h) {
      const f = floors.find((x) => x.id === focusId)
      if (mode === "floor" && f && isPlan(f.model)) f.model.afterRender(camera, w, h)
    },
    setEvidence(per, hoveredRoom, selectedRoom, numberOf) {
      for (const f of floors) {
        if (!isPlan(f.model)) continue
        const ev = per.get(f.id)
        const focused = f.id === focusId
        f.model.setHighlight({
          evidence: ev?.rooms ?? new Set(),
          hovered: focused ? hoveredRoom : null,
          selected: focused ? selectedRoom : null,
        })
        // Pins show on every floor in the stack (that is the point of a cross-floor answer), on one floor in focus.
        f.model.setPins(mode === 'stack' || focused ? (ev?.boxes ?? []) : [], numberOf && ((b) => numberOf(b)))
      }
    },
    setCounts(counts) {
      for (const f of floors) {
        const n = counts.get(f.id) ?? 0
        f.countEl.hidden = n === 0
        f.countEl.textContent = String(n)
      }
    },
    setRoomLabels(on) {
      roomLabels = on
      retarget()
    },
    floorOf(o) {
      for (let x: THREE.Object3D | null = o; x; x = x.parent) if (typeof x.userData.floorId === 'string') return x.userData.floorId
      return null
    },
    dispose() {
      for (const f of floors) {
        f.labelObj.removeFromParent()
        f.labelObj.element.remove()
        f.model.dispose()
      }
      group.removeFromParent()
    },
  }
  retarget()
  for (const f of floors) {
    f.root.position.y = f.y.x
    f.appliedFade = -1
    applyFade(f)
  }
  return model
}

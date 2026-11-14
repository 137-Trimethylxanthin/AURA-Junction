import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { CSS2DRenderer } from 'three/examples/jsm/renderers/CSS2DRenderer.js'
import { Spring, springStep } from './spring'

export type CameraPreset = 'persp' | 'top'

interface Pose {
  pos: THREE.Vector3
  target: THREE.Vector3
}

/** Where the camera is heading: springs pull position and orbit target toward it (interruptible). */
interface CamGoal extends Pose {
  response: number
  /** performance.now() before which the move holds (the intro lets the first frame paint). */
  startAt: number
}

/** Visible on screen: the object and every ancestor (the raycaster itself ignores visibility). */
function shown(o: THREE.Object3D | null): boolean {
  for (; o; o = o.parent) if (!o.visible) return false
  return true
}

/** What to frame: a centre, a radius to fit, and optionally the direction the camera looks from. */
export interface Framing {
  center: THREE.Vector3
  radius: number
  dir?: THREE.Vector3
}

/** Something the engine shows: a group, its size, and a wall height it can animate. */
export interface Showable {
  group: THREE.Group
  size: { w: number; d: number; radius: number }
  setWallHeight(h: number): void
  afterRender?(camera: THREE.Camera, viewW: number, viewH: number): void
  /** Advance the model's own animation by dt seconds; true while anything still moves. */
  tick?(dt: number): boolean
}

/**
 * Renderer, camera, orbit controls, lights and the frame loop. Knows nothing about drawings:
 * the React view hands it a Showable and reads picks back.
 */
export class Engine {
  readonly renderer: THREE.WebGLRenderer
  readonly labels: CSS2DRenderer
  readonly scene = new THREE.Scene()
  readonly camera = new THREE.PerspectiveCamera(38, 1, 0.05, 2000)
  readonly controls: OrbitControls
  private sun = new THREE.DirectionalLight('#ffffff', 2.4)
  private ground: THREE.Mesh
  private current: Showable | null = null
  private goal: CamGoal | null = null
  private camVel = { pos: new THREE.Vector3(), target: new THREE.Vector3() }
  private wall = new Spring(2.6, 0.5)
  private wallStartAt = 0
  private last = performance.now()
  private raf = 0
  private ro: ResizeObserver
  private raycaster = new THREE.Raycaster()
  private reduced: boolean
  /** Frames are drawn only when something changed: a still model costs nothing (battery, CI, other tabs). */
  private dirty = true

  private host: HTMLElement

  constructor(host: HTMLElement) {
    this.host = host
    this.reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: false })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    this.renderer.shadowMap.enabled = true
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap
    this.renderer.toneMapping = THREE.NeutralToneMapping
    this.renderer.domElement.className = 'v3d-canvas'
    host.appendChild(this.renderer.domElement)

    this.labels = new CSS2DRenderer()
    this.labels.domElement.className = 'v3d-labels'
    host.appendChild(this.labels.domElement)

    this.controls = new OrbitControls(this.camera, this.renderer.domElement)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.08
    this.controls.maxPolarAngle = Math.PI * 0.49
    this.controls.screenSpacePanning = false
    this.controls.autoRotateSpeed = 0.7
    // Any user input stops a running camera move.
    // Any user input takes the camera over at once; the wall height keeps settling on its own.
    this.controls.addEventListener('start', () => this.stopCamera())
    this.controls.addEventListener('change', () => this.invalidate())

    this.scene.add(new THREE.HemisphereLight('#ffffff', '#cfc9bf', 1.5))
    this.sun.castShadow = true
    this.sun.shadow.mapSize.set(2048, 2048)
    this.sun.shadow.bias = -0.0004
    this.sun.shadow.normalBias = 0.02
    this.scene.add(this.sun, this.sun.target)

    this.ground = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.ShadowMaterial({ opacity: 0.1 }))
    this.ground.rotation.x = -Math.PI / 2
    this.ground.position.y = -0.17
    this.ground.receiveShadow = true
    this.scene.add(this.ground)

    this.ro = new ResizeObserver(() => this.resize())
    this.ro.observe(host)
    this.resize()
    this.loop()
  }

  private resize() {
    const w = Math.max(1, this.host.clientWidth)
    const h = Math.max(1, this.host.clientHeight)
    this.renderer.setSize(w, h, false)
    this.renderer.domElement.style.width = `${w}px`
    this.renderer.domElement.style.height = `${h}px`
    this.labels.setSize(w, h)
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
    this.invalidate()
  }

  /** Ask for one more frame (after changing the model from outside). */
  invalidate() {
    this.dirty = true
  }

  private loop = () => {
    this.raf = requestAnimationFrame(this.loop)
    const now = performance.now()
    // The closed-form spring step is exact for any dt, so slow devices keep real-time motion (a small cap would
    // stretch every animation there). The cap only stops a backgrounded tab from jumping on return.
    const dt = Math.min(0.25, Math.max(0, (now - this.last) / 1000))
    this.last = now
    if (this.goal && now >= this.goal.startAt) {
      if (this.stepCamera(dt, this.goal)) this.dirty = true
      else this.goal = null
    }
    if (now >= this.wallStartAt && !this.wall.settled()) {
      this.wall.step(dt)
      this.current?.setWallHeight(this.wall.x)
      this.dirty = true
    }
    if (this.current?.tick?.(dt)) this.dirty = true
    // update() is true while damping or auto-rotation still moves the camera.
    if (this.controls.update()) this.dirty = true
    if (!this.dirty) return
    this.dirty = false
    this.renderer.render(this.scene, this.camera)
    this.labels.render(this.scene, this.camera)
    this.current?.afterRender?.(this.camera, this.host.clientWidth, this.host.clientHeight)
  }

  /** One spring step for camera position and target, per axis; false once both have arrived. */
  private stepCamera(dt: number, g: CamGoal): boolean {
    let moving = false
    for (const [obj, goal, vel] of [
      [this.camera.position, g.pos, this.camVel.pos],
      [this.controls.target, g.target, this.camVel.target],
    ] as const) {
      for (const k of ['x', 'y', 'z'] as const) {
        const [x, v] = springStep(obj[k], vel[k], goal[k], dt, g.response)
        obj[k] = x
        vel[k] = v
        if (Math.abs(x - goal[k]) > 1e-3 || Math.abs(v) > 1e-2) moving = true
      }
    }
    if (!moving) {
      this.camera.position.copy(g.pos)
      this.controls.target.copy(g.target)
      this.camVel.pos.set(0, 0, 0)
      this.camVel.target.set(0, 0, 0)
    }
    return moving
  }

  private stopCamera() {
    this.goal = null
    this.camVel.pos.set(0, 0, 0)
    this.camVel.target.set(0, 0, 0)
  }

  private moveTo(p: Pose, response: number, delayMs = 0) {
    if (this.reduced) {
      this.stopCamera()
      this.camera.position.copy(p.pos)
      this.controls.target.copy(p.target)
      this.dirty = true
      return
    }
    // From wherever the camera is now, keeping its velocity: a new goal mid-flight blends, never jumps.
    this.goal = { pos: p.pos.clone(), target: p.target.clone(), response, startAt: performance.now() + delayMs }
    this.dirty = true
  }

  /** Camera distance that fits a footprint of radius r in the current viewport. */
  private fitDistance(r: number) {
    const vfov = THREE.MathUtils.degToRad(this.camera.fov)
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * this.camera.aspect)
    return (r * 0.95) / Math.sin(Math.min(vfov, hfov) / 2)
  }

  private pose(
    preset: CameraPreset,
    center = new THREE.Vector3(),
    radius = this.current?.size.radius ?? 10,
    view = new THREE.Vector3(0.35, 0.95, 0.85),
  ): Pose {
    const dist = this.fitDistance(radius)
    if (preset === 'top') return { pos: center.clone().add(new THREE.Vector3(0, dist, 0.001)), target: center.clone() }
    const dir = view.clone().normalize()
    return {
      pos: center.clone().add(dir.multiplyScalar(dist * 0.92)),
      target: center.clone().add(new THREE.Vector3(0, -0.2, 0)),
    }
  }

  /** Replace what is shown. With `intro`, the model starts as the flat plan seen from above and rises. */
  show(
    model: Showable,
    wallH: number,
    intro: boolean,
    framing?: Framing,
    fov = 38,
  ) {
    if (this.current) this.scene.remove(this.current.group)
    this.current = model
    // A long lens flattens perspective: a tall stack of floors reads like an axonometric drawing.
    this.camera.fov = fov
    this.camera.updateProjectionMatrix()
    this.scene.add(model.group)
    const { radius: r, w, d } = model.size
    this.ground.scale.set(w * 6, d * 6, 1)
    this.sun.position.set(r * 0.7, r * 1.6, r * 0.9)
    const cam = this.sun.shadow.camera
    cam.left = -r * 1.2
    cam.right = r * 1.2
    cam.top = r * 1.2
    cam.bottom = -r * 1.2
    cam.near = 0.1
    cam.far = r * 5
    cam.updateProjectionMatrix()
    this.camera.near = Math.max(0.05, r / 200)
    this.camera.far = r * 20
    this.controls.minDistance = r * 0.15
    this.controls.maxDistance = r * 5
    const end = this.pose('persp', framing?.center, framing?.radius, framing?.dir)
    this.stopCamera()
    if (intro && !this.reduced) {
      // Start as the flat drawing seen from above, then tilt into 3D while the walls rise.
      const start = this.pose('top', framing?.center, framing?.radius, framing?.dir)
      this.camera.position.copy(start.pos)
      this.controls.target.copy(start.target)
      this.wall.snap(0.001)
      this.wall.target = wallH
      this.wall.response = 0.9
      this.wallStartAt = performance.now() + 450
      model.setWallHeight(0.001)
      this.moveTo(end, 1.1, 250)
    } else {
      this.camera.position.copy(end.pos)
      this.controls.target.copy(end.target)
      this.wall.snap(wallH)
      model.setWallHeight(wallH)
    }
    this.last = performance.now()
    this.controls.update()
    this.dirty = true
  }

  /** Take a model off the stage (before it is disposed). */
  clear(model: Showable) {
    if (this.current !== model) return
    this.scene.remove(model.group)
    this.current = null
    this.stopCamera()
    this.dirty = true
  }

  /** Wall height follows a spring, so dragging the slider feels physical and can be reversed mid-way. */
  setWallHeight(h: number) {
    this.wall.target = h
    this.wall.response = 0.35
    if (this.reduced) {
      this.wall.snap(h)
      this.current?.setWallHeight(h)
    }
    this.dirty = true
  }

  goTo(preset: CameraPreset, framing?: Framing) {
    this.moveTo(this.pose(preset, framing?.center, framing?.radius, framing?.dir), 0.55)
  }

  setSpin(on: boolean) {
    this.controls.autoRotate = on && !this.reduced
  }

  /**
   * First mesh under a client point whose userData has `key`, and that value. `accept` can rule objects out
   * (e.g. floors that are faded away); those let the ray pass, like glass.
   */
  pick(clientX: number, clientY: number, key: string, accept?: (o: THREE.Object3D) => boolean): string | null {
    if (!this.current) return null
    const rect = this.renderer.domElement.getBoundingClientRect()
    const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1)
    this.raycaster.setFromCamera(ndc, this.camera)
    for (const hit of this.raycaster.intersectObject(this.current.group, true)) {
      if (!(hit.object as THREE.Mesh).isMesh || !shown(hit.object)) continue
      if (accept && !accept(hit.object)) continue
      const v = hit.object.userData[key]
      if (typeof v === 'string') return v
      // Walls and fixtures block the floor behind them, but glass does not.
      const mat = (hit.object as THREE.Mesh).material as THREE.Material
      if (!Array.isArray(mat) && mat.transparent) continue
      return null
    }
    return null
  }

  dispose() {
    cancelAnimationFrame(this.raf)
    this.ro.disconnect()
    this.controls.dispose()
    if (this.current) this.scene.remove(this.current.group)
    this.ground.geometry.dispose()
    ;(this.ground.material as THREE.Material).dispose()
    this.sun.dispose() // frees the shadow map
    this.renderer.dispose()
    // Hand the GL context back now rather than at GC: every 2D→3D toggle makes a new one.
    this.renderer.forceContextLoss()
    this.renderer.domElement.remove()
    this.labels.domElement.remove()
  }
}

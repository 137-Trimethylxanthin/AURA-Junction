import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { CSS2DRenderer } from 'three/examples/jsm/renderers/CSS2DRenderer.js'

export type CameraPreset = 'persp' | 'top'

interface Pose {
  pos: THREE.Vector3
  target: THREE.Vector3
}

interface Tween {
  from: Pose & { h: number }
  to: Pose & { h: number }
  t0: number
  dur: number
}

/** Visible on screen: the object and every ancestor (the raycaster itself ignores visibility). */
function shown(o: THREE.Object3D | null): boolean {
  for (; o; o = o.parent) if (!o.visible) return false
  return true
}

const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

/** Something the engine shows: a group, its size, and a wall height it can animate. */
export interface Showable {
  group: THREE.Group
  size: { w: number; d: number; radius: number }
  setWallHeight(h: number): void
  afterRender?(camera: THREE.Camera, viewW: number, viewH: number): void
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
  private tween: Tween | null = null
  private wallH = 2.6
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
    this.controls.addEventListener('start', () => {
      if (this.tween) this.finishTween()
    })
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
    if (this.tween) {
      const t = Math.min(1, (performance.now() - this.tween.t0) / this.tween.dur)
      const k = ease(t)
      const { from, to } = this.tween
      this.camera.position.lerpVectors(from.pos, to.pos, k)
      this.controls.target.lerpVectors(from.target, to.target, k)
      this.current?.setWallHeight(from.h + (to.h - from.h) * k)
      if (t >= 1) this.tween = null
      this.dirty = true
    }
    // update() is true while damping or auto-rotation still moves the camera.
    if (this.controls.update()) this.dirty = true
    if (!this.dirty) return
    this.dirty = false
    this.renderer.render(this.scene, this.camera)
    this.labels.render(this.scene, this.camera)
    this.current?.afterRender?.(this.camera, this.host.clientWidth, this.host.clientHeight)
  }

  private finishTween() {
    if (!this.tween) return
    this.current?.setWallHeight(this.tween.to.h)
    this.tween = null
    this.dirty = true
  }

  /** Camera distance that fits a footprint of radius r in the current viewport. */
  private fitDistance(r: number) {
    const vfov = THREE.MathUtils.degToRad(this.camera.fov)
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * this.camera.aspect)
    return (r * 0.95) / Math.sin(Math.min(vfov, hfov) / 2)
  }

  private pose(preset: CameraPreset): Pose {
    const r = this.current?.size.radius ?? 10
    const dist = this.fitDistance(r)
    if (preset === 'top') return { pos: new THREE.Vector3(0, dist, 0.001), target: new THREE.Vector3() }
    const dir = new THREE.Vector3(0.35, 0.95, 0.85).normalize()
    return { pos: dir.multiplyScalar(dist * 0.92), target: new THREE.Vector3(0, -0.2, 0) }
  }

  /** Replace what is shown. With `intro`, the model starts as the flat plan seen from above and rises. */
  show(model: Showable, wallH: number, intro: boolean) {
    if (this.current) this.scene.remove(this.current.group)
    this.current = model
    this.wallH = wallH
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
    const end = this.pose('persp')
    if (intro && !this.reduced) {
      const start = this.pose('top')
      this.camera.position.copy(start.pos)
      this.controls.target.copy(start.target)
      model.setWallHeight(0.001)
      this.tween = { from: { ...start, h: 0.001 }, to: { ...end, h: wallH }, t0: performance.now() + 250, dur: 1900 }
    } else {
      this.tween = null
      this.camera.position.copy(end.pos)
      this.controls.target.copy(end.target)
      model.setWallHeight(wallH)
    }
    this.controls.update()
    this.dirty = true
  }

  /** Take a model off the stage (before it is disposed). */
  clear(model: Showable) {
    if (this.current !== model) return
    this.scene.remove(model.group)
    this.current = null
    this.tween = null
    this.dirty = true
  }

  setWallHeight(h: number) {
    this.wallH = h
    if (this.tween) this.tween.to.h = h
    else this.current?.setWallHeight(h)
    this.dirty = true
  }

  goTo(preset: CameraPreset) {
    const to = this.pose(preset)
    const from = { pos: this.camera.position.clone(), target: this.controls.target.clone(), h: this.wallH }
    if (this.reduced) {
      this.camera.position.copy(to.pos)
      this.controls.target.copy(to.target)
      this.dirty = true
      return
    }
    this.tween = { from, to: { ...to, h: this.wallH }, t0: performance.now(), dur: 900 }
  }

  setSpin(on: boolean) {
    this.controls.autoRotate = on && !this.reduced
  }

  /** First mesh under a client point whose userData has `key`, and that value. */
  pick(clientX: number, clientY: number, key: string): string | null {
    if (!this.current) return null
    const rect = this.renderer.domElement.getBoundingClientRect()
    const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1)
    this.raycaster.setFromCamera(ndc, this.camera)
    for (const hit of this.raycaster.intersectObject(this.current.group, true)) {
      if (!(hit.object as THREE.Mesh).isMesh || !shown(hit.object)) continue
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

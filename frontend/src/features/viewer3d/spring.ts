/**
 * Critically damped springs (Apple's damping 1.0), stepped with the exact closed-form solution so any frame
 * time is stable. `response` is Apple's parameter: roughly the seconds to reach the target, not a duration.
 * Re-targeting keeps position and velocity, so every motion is interruptible without a jump.
 */
export function springStep(x: number, v: number, target: number, dt: number, response: number): [number, number] {
  if (response <= 0) return [target, 0]
  const w = (2 * Math.PI) / response
  const c1 = x - target
  const c2 = v + w * c1
  const e = Math.exp(-w * dt)
  const nx = target + (c1 + c2 * dt) * e
  const nv = (c2 - w * (c1 + c2 * dt)) * e
  return [nx, nv]
}

/** A scalar spring with its own target and response. */
export class Spring {
  x: number
  v = 0
  target: number
  response: number
  constructor(x: number, response = 0.4) {
    this.x = x
    this.target = x
    this.response = response
  }
  /** Advance by dt seconds; returns true while still moving. */
  step(dt: number): boolean {
    if (this.settled()) {
      this.x = this.target
      this.v = 0
      return false
    }
    ;[this.x, this.v] = springStep(this.x, this.v, this.target, dt, this.response)
    return true
  }
  settled(eps = 1e-4): boolean {
    return Math.abs(this.x - this.target) < eps && Math.abs(this.v) < eps * 10
  }
  snap(x = this.target) {
    this.x = x
    this.target = x
    this.v = 0
  }
}

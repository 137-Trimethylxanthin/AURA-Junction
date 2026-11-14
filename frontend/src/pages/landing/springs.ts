import type { Transition } from "motion/react"

/**
 * Motion vocabulary for the landing pages (apple-motion): critically damped springs, no bounce,
 * transform and opacity only.
 */
export const SPRING: Transition = { type: "spring", bounce: 0, duration: 0.6 }
export const SPRING_FAST: Transition = {
  type: "spring",
  bounce: 0,
  duration: 0.35,
}

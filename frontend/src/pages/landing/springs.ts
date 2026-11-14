import { useSyncExternalStore } from "react"
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

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)"

function subscribeReducedMotion(onStoreChange: () => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return () => {}
  }
  const mq = window.matchMedia(REDUCED_MOTION_QUERY)
  mq.addEventListener("change", onStoreChange)
  return () => mq.removeEventListener("change", onStoreChange)
}

function getReducedMotionSnapshot(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false
  }
  return window.matchMedia(REDUCED_MOTION_QUERY).matches
}

/**
 * Live-updating `prefers-reduced-motion: reduce` hook (`motion/react`'s `useReducedMotion` only reads once on mount).
 */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeReducedMotion,
    getReducedMotionSnapshot,
    () => false
  )
}

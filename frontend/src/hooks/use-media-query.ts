import { useSyncExternalStore } from "react"

/** Subscribes to a CSS media query. Server/first render falls back to `fallback`. */
export function useMediaQuery(query: string, fallback = false): boolean {
  return useSyncExternalStore(
    (notify) => {
      const mql = window.matchMedia(query)
      mql.addEventListener("change", notify)
      return () => mql.removeEventListener("change", notify)
    },
    () => window.matchMedia(query).matches,
    () => fallback
  )
}

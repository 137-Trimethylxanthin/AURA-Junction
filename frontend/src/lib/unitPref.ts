import { useSyncExternalStore } from 'react'

/** How answers show lengths and areas: metric (default) or imperial (ft, sq ft). Kept in this browser. */
export type UnitSystem = 'metric' | 'imperial'
const KEY = 'aura.units'
const listeners = new Set<() => void>()

function read(): UnitSystem {
  try {
    return globalThis.localStorage?.getItem(KEY) === 'imperial' ? 'imperial' : 'metric'
  } catch {
    return 'metric'
  }
}

let current: UnitSystem = read()

export const unitPref = {
  get: (): UnitSystem => current,
  set(u: UnitSystem) {
    current = u
    try {
      globalThis.localStorage?.setItem(KEY, u)
    } catch {
      // private mode: the choice lasts for this page only
    }
    listeners.forEach((fn) => fn())
  },
  subscribe(fn: () => void) {
    listeners.add(fn)
    return () => listeners.delete(fn)
  },
}

/** Hook: re-renders when the unit system changes. */
export function useUnitPref(): UnitSystem {
  return useSyncExternalStore(unitPref.subscribe, unitPref.get, () => 'metric' as UnitSystem)
}

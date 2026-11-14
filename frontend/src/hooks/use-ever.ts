import { useState } from 'react'

/** True from the first render where `value` was true: mount a lazily loaded feature once, then keep it. */
export function useEver(value: boolean): boolean {
  const [ever, setEver] = useState(value)
  if (value && !ever) setEver(true)
  return ever || value
}

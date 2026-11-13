import { useEffect } from 'react'

/** Sets document.title for the current route ("<page> · AURA"). */
export function useTitle(page: string | null) {
  useEffect(() => {
    document.title = page ? `${page} · AURA` : 'AURA · Automated Understanding of Rooms & Areas'
  }, [page])
}

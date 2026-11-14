import { useEffect } from 'react'

const DEFAULT_TITLE = 'AURA · Automated Understanding of Rooms & Areas'

/** Sets document.title for the current route ("<page> · AURA", or verbatim when `opts.exact` is true). */
export function useTitle(page: string | null, opts?: { exact?: boolean }) {
  const exact = opts?.exact ?? false
  useEffect(() => {
    document.title = !page ? DEFAULT_TITLE : exact ? page : `${page} · AURA`
  }, [page, exact])
}

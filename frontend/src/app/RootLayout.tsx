import { useEffect } from "react"
import {
  Outlet,
  ScrollRestoration,
  useLocation,
  useNavigate,
} from "react-router"
import { toast } from "sonner"
import { Spinner } from "@/components/ui/spinner"
import { Toaster } from "@/components/ui/sonner"
import { useMediaQuery } from "@/hooks/use-media-query"
import { presenter } from "@/api/library"
import { tu } from "@/i18n/uiLang"

/**
 * A `#presenter=<token>` link (or the older `?presenter=`) stores the presenter token in this browser (never in
 * the bundle) and is then removed from the address bar, so it does not end up in history, screenshots or shared
 * links. Prefer the #fragment: browsers never send it to a server, so it stays out of request logs and Referer
 * headers. The token is only ever sent as the X-AURA-Presenter header of POST /uploads.
 */
function usePresenterLink() {
  const { pathname, search, hash } = useLocation()
  const navigate = useNavigate()
  useEffect(() => {
    const params = new URLSearchParams(search)
    // Only a hash that is itself "presenter=…" is read as parameters; any other hash is an anchor and stays as is.
    const frag = /^#presenter=/.test(hash) ? new URLSearchParams(hash.slice(1)) : null
    const token = frag?.get("presenter") ?? params.get("presenter")
    if (token === null || token === undefined) return
    params.delete("presenter")
    if (token.trim()) {
      presenter.set(token.trim().slice(0, 512))
      toast.success(tu("presenter_on"))
    }
    const rest = params.toString()
    void navigate(
      { pathname, search: rest ? `?${rest}` : "", hash: frag ? "" : hash },
      { replace: true }
    )
  }, [pathname, search, hash, navigate])
}

/**
 * Each top-level section (landing, library, workspace, …) settles in with a short critically damped rise.
 * Entrance only: the old page is gone at once, so nothing ever blocks input. Reduced motion: a quick fade.
 * (Styles: .page-enter in index.css.)
 */
function PageTransition() {
  const section = useLocation().pathname.split('/')[1] ?? ''
  // Entrance only, never gesture-driven, so a CSS animation is enough and keeps motion out of the entry chunk.
  return (
    <div key={section} className="page-enter min-h-dvh">
      <Outlet />
    </div>
  )
}

export function RootLayout() {
  const narrow = useMediaQuery("(max-width: 899px)")
  usePresenterLink()
  return (
    <>
      <PageTransition />
      <Toaster position={narrow ? "bottom-center" : "bottom-right"} />
      <ScrollRestoration />
    </>
  )
}

/** Shown while the first route's code loads. */
export function RootFallback() {
  return (
    <div
      className="flex h-dvh items-center justify-center bg-background text-muted-foreground"
      aria-busy="true"
    >
      <Spinner className="size-6" />
    </div>
  )
}

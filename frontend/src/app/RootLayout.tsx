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

/**
 * A `?presenter=<token>` link stores the presenter token in this browser (never in the bundle) and is then
 * removed from the address bar, so it does not end up in history, screenshots or shared links.
 */
function usePresenterLink() {
  const { pathname, search, hash } = useLocation()
  const navigate = useNavigate()
  useEffect(() => {
    const params = new URLSearchParams(search)
    const token = params.get("presenter")
    if (token === null) return
    params.delete("presenter")
    if (token.trim()) {
      presenter.set(token.trim())
      toast.success("Presenter access is on in this browser.")
    }
    const rest = params.toString()
    void navigate(
      { pathname, search: rest ? `?${rest}` : "", hash },
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

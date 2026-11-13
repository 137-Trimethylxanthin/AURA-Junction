import { useEffect, type ReactNode } from "react"
import { Link } from "react-router"
import { ArrowRightIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { APP_ROUTE, ISSUES_URL, REPO_URL } from "./links"
import "./landing.css"

export function useTitle(title: string) {
  useEffect(() => {
    const prev = document.title
    document.title = title
    return () => {
      document.title = prev
    }
  }, [title])
}

/** The AURA mark: a two-room plan outline, the right room filled in the evidence colour. */
export function AuraMark({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className={cn("size-6 shrink-0 text-foreground", className)}
      fill="none"
    >
      <path
        d="M13 4h5.5A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5H13z"
        className="fill-info"
      />
      <path
        d="M5.5 4h13A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5v-13A1.5 1.5 0 0 1 5.5 4zM13 4v6.5M13 14.5V20"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="16.5" cy="12" r="2" className="fill-background" />
    </svg>
  )
}

export const WRAP = "wrap mx-auto w-full max-w-[80rem] px-4 sm:px-6 lg:px-10"

/** Page-level buttons: shadcn Button, a step taller than app chrome, with the press scale. */
export const PAGE_BTN = "l-press h-11 px-4"

export function SiteHeader({
  nav,
}: {
  nav?: { href: string; label: string }[]
}) {
  return (
    <header className="l-header sticky top-0 z-30">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground focus:not-sr-only focus:absolute focus:top-3 focus:left-3"
      >
        Skip to content
      </a>
      <div className={cn(WRAP, "flex h-16 items-center gap-6")}>
        <Link
          to="/"
          className="flex min-h-11 items-center gap-2 rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring"
          aria-label="AURA home"
        >
          <AuraMark />
          <span className="text-sm font-semibold tracking-[0.08em]">AURA</span>
        </Link>
        {nav?.length ? (
          <nav
            aria-label="Sections"
            className="hidden items-center gap-1 md:flex"
          >
            {nav.map((n) => (
              <Button
                key={n.href}
                asChild
                variant="ghost"
                className="l-press text-muted-foreground focus-visible:ring-ring"
              >
                <a href={n.href}>{n.label}</a>
              </Button>
            ))}
          </nav>
        ) : null}
        <Button asChild className={cn(PAGE_BTN, "ml-auto")}>
          <Link to={APP_ROUTE}>
            Open the app
            <ArrowRightIcon data-icon="inline-end" />
          </Link>
        </Button>
      </div>
    </header>
  )
}

export function SiteFooter() {
  const links: { label: string; to?: string; href?: string }[] = [
    { label: "App", to: APP_ROUTE },
    { label: "Credits", to: "/credits" },
    { label: "Privacy", to: "/privacy" },
    { label: "Source code (MIT)", href: REPO_URL },
    { label: "Report a problem", href: ISSUES_URL },
  ]
  return (
    <footer className="border-t bg-background">
      <div className={cn(WRAP, "flex flex-col gap-6 py-10")}>
        <div className="flex flex-col gap-6 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex max-w-sm flex-col gap-2">
            <div className="flex items-center gap-2">
              <AuraMark />
              <span className="text-sm font-semibold tracking-[0.08em]">
                AURA
              </span>
            </div>
            <p className="text-sm text-muted-foreground">
              Automated Understanding of Rooms &amp; Areas. Built at Junction
              2026 for the Stoa challenge “Make Blueprints Talk”.
            </p>
          </div>
          <nav
            aria-label="Footer"
            className="-mx-3 grid grid-cols-2 sm:flex sm:flex-wrap"
          >
            {links.map((l) => (
              <Button
                key={l.label}
                asChild
                variant="ghost"
                className="l-press justify-start text-muted-foreground focus-visible:ring-ring"
              >
                {l.to ? (
                  <Link to={l.to}>{l.label}</Link>
                ) : (
                  <a href={l.href} rel="noreferrer">
                    {l.label}
                  </a>
                )}
              </Button>
            ))}
          </nav>
        </div>
        <p className="border-t pt-4 text-xs text-muted-foreground">
          Sample drawing from{" "}
          <Link
            to="/credits"
            className="text-foreground underline decoration-border underline-offset-4 outline-none hover:decoration-foreground focus-visible:ring-3 focus-visible:ring-ring"
          >
            Swiss Dwellings v3.0.0, Archilyse AG, CC BY 4.0
          </Link>
          . Labels and numbering are synthetic. Not affiliated with Stoa.
        </p>
      </div>
    </footer>
  )
}

/** Page frame for the landing, credits and privacy routes: own header and footer, document scroll. */
export function PageFrame({
  children,
  nav,
}: {
  children: ReactNode
  nav?: { href: string; label: string }[]
}) {
  return (
    <div className="l-page flex min-h-dvh flex-col bg-background text-foreground">
      <SiteHeader nav={nav} />
      <main id="main" tabIndex={-1} className="flex-1 outline-none">
        {children}
      </main>
      <SiteFooter />
    </div>
  )
}

import { NavLink } from 'react-router'
import { cn } from '@/lib/utils'
import { Wordmark } from './Wordmark'

const NAV = [
  { to: '/app', label: 'Drawings' },
  { to: '/credits', label: 'Credits' },
  { to: '/privacy', label: 'Privacy' },
] as const

/** Header for the pages around the workspace (library, 404, errors). */
export function SiteHeader() {
  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-background px-4 pt-[env(safe-area-inset-top)]">
      <NavLink to="/" aria-label="AURA home" className="-mx-1 flex h-10 items-center rounded-md px-1">
        <Wordmark />
      </NavLink>
      <nav aria-label="Site" className="ml-auto flex items-center gap-1">
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            className={({ isActive }) =>
              cn(
                'flex h-10 items-center rounded-md px-3 text-sm text-muted-foreground transition-colors [@media(hover:hover)]:hover:bg-muted [@media(hover:hover)]:hover:text-foreground',
                isActive && 'font-medium text-foreground',
              )
            }
          >
            {n.label}
          </NavLink>
        ))}
      </nav>
    </header>
  )
}

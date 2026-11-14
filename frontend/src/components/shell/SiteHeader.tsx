import { NavLink } from 'react-router'
import { cn } from '@/lib/utils'
import { Wordmark } from './Wordmark'
import { LanguageMenu } from './LanguageMenu'
import { useUi } from '@/i18n/uiLang'
import type { UiKey } from '@/i18n/strings'

const NAV: { to: string; key: UiKey }[] = [
  { to: '/app', key: 'nav_drawings' },
  { to: '/credits', key: 'nav_credits' },
  { to: '/privacy', key: 'nav_privacy' },
]

/** Header for the pages around the workspace (library, 404, errors). */
export function SiteHeader() {
  const { tu } = useUi()
  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-background px-4 pt-[env(safe-area-inset-top)]">
      <NavLink to="/" aria-label={tu('site_home')} className="-mx-1 flex h-10 items-center rounded-md px-1">
        <Wordmark />
      </NavLink>
      <nav aria-label={tu('site_nav')} className="ml-auto flex items-center gap-1">
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
            {tu(n.key)}
          </NavLink>
        ))}
        <LanguageMenu />
      </nav>
    </header>
  )
}

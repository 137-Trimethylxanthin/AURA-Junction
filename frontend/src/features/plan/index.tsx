import { appStore, useAppStore } from '@/store'
import { licenceLabel } from '@/api/contract'
import './plan.css'

export { PlanStage } from './PlanStage'
export { PlanToolbar, SourceBadge } from './PlanToolbar'

// Dev-only test hook for Playwright: drive the store from the page.
if (import.meta.env.DEV) (window as unknown as { __aura: typeof appStore }).__aura = appStore

/** Dataset credit and licence: always visible, wraps to a second line where needed. */
export function Credit() {
  const credit = useAppStore((s) => s.session.drawing.credit)
  return (
    // Wraps rather than truncates: the full attribution stays readable without interaction.
    <p className="min-w-0 leading-snug text-pretty">
      <span>{credit.text}</span>{' '}
      {credit.url ? (
        <a
          href={credit.url}
          target="_blank"
          rel="noreferrer"
          className="rounded-sm font-medium whitespace-nowrap text-foreground underline underline-offset-2"
        >
          {licenceLabel(credit)}
        </a>
      ) : (
        <span className="font-medium whitespace-nowrap text-foreground">{licenceLabel(credit)}</span>
      )}
    </p>
  )
}

import { useMemo } from 'react'
import type { Scope } from '@/api/contract'
import { useUi } from '@/i18n/uiLang'
import { cn } from '@/lib/utils'
import { actions, selectActiveProject, selectCrossPlanScope, selectDrawing, selectIsPending, useAppStore } from '@/store'
import { suggestionsFor } from './draft'

/** Suggested questions with a language tag. `row` = one horizontally scrolling line (composer). */
export function SuggestionList({ row = false }: { row?: boolean }) {
  const pending = useAppStore(selectIsPending)
  const drawing = useAppStore(selectDrawing)
  const activeProject = useAppStore(selectActiveProject)
  const crossPlanScope = useAppStore(selectCrossPlanScope)
  const { tu } = useUi()

  const list = useMemo(() => suggestionsFor(drawing, activeProject), [drawing, activeProject])

  return (
    <ul
      aria-label={tu('sugg_aria')}
      className={cn(
        'flex gap-1.5',
        row ? 'shrink-0' : 'flex-col items-start',
      )}
    >
      {list.map((s) => (
        <li key={s.text} className={row ? 'shrink-0' : 'max-w-full'}>
          <button
            type="button"
            title={s.text}
            disabled={pending}
            onClick={() => {
              const useProjectScope =
                activeProject &&
                activeProject.floors.length >= 2 &&
                (crossPlanScope || /across all floors/i.test(s.text))
              if (useProjectScope) {
                const projectScope: Scope = {
                  kind: 'project',
                  project_id: activeProject.id,
                  drawing_ids: activeProject.floors.map((f) => f.drawing_id),
                }
                void actions().ask(s.text, { scope: projectScope })
              } else {
                void actions().ask(s.text)
              }
            }}
            className={cn(
              'inline-flex min-h-10 max-w-full items-center gap-2 rounded-lg border bg-background px-2.5 text-left text-sm transition-colors outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50',
            )}
          >
            <span className="num text-xs font-semibold text-muted-foreground">{s.lang}</span>
            <span className={row ? 'whitespace-nowrap' : 'py-2 text-pretty'}>{row ? s.short : s.text}</span>
          </button>
        </li>
      ))}
    </ul>
  )
}

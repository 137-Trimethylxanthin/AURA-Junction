import { useUi } from "@/i18n/uiLang"
import { budgetLeft, useBudget } from "./budget"

export function BudgetMeter() {
  const { tu } = useUi()
  const b = useBudget()
  if (!b) return null
  if (b.models === "off")
    return (
      <span
        role="status"
        aria-label={tu("budget_off_aria")}
        title={tu("budget_off_aria")}
        data-budget-models="off"
        className="hidden text-xs text-muted-foreground md:inline"
      >
        {tu("budget_off")}
      </span>
    )
  const { left, cap } = budgetLeft(b)
  const used = cap > 0 ? 1 - left / cap : 1
  const label = tu("budget_aria", { left, cap })
  return (
    <span
      role="status"
      aria-label={label}
      title={label}
      data-budget-left={left}
      className="hidden items-center gap-2 px-1 text-xs text-muted-foreground md:flex"
    >
      <span aria-hidden className="relative h-1 w-8 overflow-hidden rounded-full bg-muted">
        <span
          className="absolute inset-y-0 left-0 w-full origin-left rounded-full bg-muted-foreground/70 motion-safe:transition-transform motion-safe:duration-300 ease-out"
          style={{ transform: `scaleX(${Math.min(1, Math.max(0, used))})` }}
        />
      </span>
      <span className="num">{tu("budget_short", { left, cap })}</span>
    </span>
  )
}

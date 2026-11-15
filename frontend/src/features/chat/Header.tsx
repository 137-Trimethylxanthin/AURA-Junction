import { useUi } from "@/i18n/uiLang"
import { localizeSubtitle } from "@/lib/subtitle"
import { Suspense, lazy, useMemo } from "react"
import { BlocksIcon, HistoryIcon, SquarePenIcon } from "lucide-react"
import { toast } from "sonner"
import { displayDrawingTitle } from "@/api/library"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { actions, selectActiveProject, selectActiveTurn, selectDrawing, useAppStore } from "@/store"
import { useEver } from "@/hooks/use-ever"
import { openSessionHistory } from "@/features/history"
import { budgetTotals, useBudget } from "@/features/budget/budget"

/** Loaded the first time the Plugins sheet opens. */
const PluginsSheet = lazy(() =>
  import("@/features/plugins/PluginsSheet").then((m) => ({
    default: m.PluginsSheet,
  }))
)
import { callsText, fmtUsd, roundCost } from "./view"

const CHAT_TOASTER = "chat"
const NEW_CHAT_UNDO_TOAST = "new-chat-undo"

export function DrawingTitle() {
  const d = useAppStore(selectDrawing)
  const { lang: uiLanguage } = useUi()
  const activeProject = useAppStore(selectActiveProject)
  const floorLabel = useMemo(() => {
    const fromProject = activeProject?.floors.find((f) => f.drawing_id === d.id)?.label
    if (fromProject) return fromProject
    return typeof d.floor_label === "string" && d.floor_label.trim() ? d.floor_label.trim() : null
  }, [activeProject, d])
  const titleText = activeProject ? activeProject.title || displayDrawingTitle(d) : displayDrawingTitle(d)

  return (
    <h1 className="m-0 flex min-w-0 items-baseline gap-2 text-sm font-normal">
      <span className="truncate font-medium text-foreground">{titleText}</span>
      {floorLabel ? (
        <span
          data-header-floor
          className="num shrink-0 rounded bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground"
        >
          {floorLabel}
        </span>
      ) : null}
      {d.subtitle ? (
        <span className="num hidden truncate text-xs text-muted-foreground sm:inline" data-header-subtitle>
          {localizeSubtitle(d.subtitle, uiLanguage)}
        </span>
      ) : null}
    </h1>
  )
}

function SessionButton() {
  const { lang, tu } = useUi()
  const turns = useAppStore((s) => s.turns)
  const spend = useAppStore((s) => s.session.spend)
  const active = useAppStore(selectActiveTurn)
  const total = budgetTotals(useBudget())
  const { calls, cost } = useMemo(() => {
    let calls = spend.calls
    let cost = roundCost(spend.cost_usd)
    for (const t of turns)
      if (t.role === "agent" && t.response) {
        // The answer's own calls plus its check's requests (contract 1.1 reports them separately).
        calls += t.response.calls + (t.response.check?.calls ?? 0)
        cost +=
          roundCost(t.response.cost_usd) +
          roundCost(t.response.check?.cost_usd ?? 0)
      }
    return { calls, cost }
  }, [turns, spend])
  const canOpen = !!active?.response
  const conv = tu("session_conv", { calls: callsText(calls, lang), cost: fmtUsd(cost) })
  const shown = total ?? { calls, cost }
  const label = total
    ? tu("session_total", { cost: fmtUsd(shown.cost), calls: callsText(shown.calls, lang) })
    : `${callsText(calls, lang)} · ${fmtUsd(cost)}`
  return (
    <Button
      variant="outline"
      disabled={!canOpen}
      // The session total is information even before there is a tree to open: keep it readable (5.7:1).
      // On a phone the header keeps Plugins and the language switch on screen: the session total stays in each
      // answer's footer and in the history sheet.
      className="hidden disabled:text-muted-foreground disabled:opacity-100 sm:inline-flex"
      data-trace-opener
      onClick={() => actions().setTraceOpen(true)}
      title={
        total
          ? `${conv}\n${canOpen ? "Open the reasoning tree for the answer on the plan" : "Ask a question to see its reasoning"}`
          : canOpen
            ? "Open the reasoning tree for the answer on the plan"
            : "Ask a question to see its reasoning"
      }
      aria-label={
        total
          ? tu("session_total_aria", { calls: callsText(shown.calls, lang), cost: fmtUsd(shown.cost) })
          : tu("session_aria", { calls: callsText(calls, lang), cost: fmtUsd(cost) })
      }
    >
      <span className="num text-xs">{label}</span>
    </Button>
  )
}

export function HeaderActions() {
  const pluginsUsed = useEver(useAppStore((s) => s.ui.pluginsOpen))
  const { tu } = useUi()
  const hasTurns = useAppStore((s) => s.turns.length > 0)
  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            aria-label={tu("new_chat")}
            disabled={!hasTurns}
            onClick={() => {
              const restore = actions().newConversation()
              toast(tu("hist_saved"), {
                id: NEW_CHAT_UNDO_TOAST,
                toasterId: CHAT_TOASTER,
                duration: 6000,
                action: {
                  label: tu("undo"),
                  onClick: () => restore(),
                },
              })
            }}
          >
            <SquarePenIcon />
          </Button>
        </TooltipTrigger>
        <TooltipContent>{tu("new_chat")}</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="ghost" size="icon" aria-label={tu("hist_open")} onClick={() => openSessionHistory()}>
            <HistoryIcon />
          </Button>
        </TooltipTrigger>
        <TooltipContent>{tu("hist_open")}</TooltipContent>
      </Tooltip>
      <SessionButton />
      <Button variant="outline" aria-label={tu("plugins")} onClick={() => actions().setPluginsOpen(true)}>
        <BlocksIcon data-icon="inline-start" />
        <span className="hidden sm:inline">{tu("plugins")}</span>
      </Button>
      {pluginsUsed ? (
        <Suspense fallback={null}>
          <PluginsSheet />
        </Suspense>
      ) : null}
    </>
  )
}

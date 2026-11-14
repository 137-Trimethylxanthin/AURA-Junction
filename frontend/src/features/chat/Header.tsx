import { useUi } from "@/i18n/uiLang"
import { Suspense, lazy, useMemo } from "react"
import { BlocksIcon, SquarePenIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { actions, selectActiveTurn, selectDrawing, useAppStore } from "@/store"
import { useEver } from "@/hooks/use-ever"

/** Loaded the first time the Plugins sheet opens. */
const PluginsSheet = lazy(() =>
  import("@/features/plugins/PluginsSheet").then((m) => ({
    default: m.PluginsSheet,
  }))
)
import { callsText, fmtUsd, roundCost } from "./view"

export function DrawingTitle() {
  const d = useAppStore(selectDrawing)
  return (
    <span className="flex min-w-0 items-baseline gap-2">
      <span className="truncate font-medium text-foreground">{d.title}</span>
      {d.subtitle ? (
        <span className="num hidden truncate text-xs text-muted-foreground sm:inline">
          {d.subtitle}
        </span>
      ) : null}
    </span>
  )
}

function SessionButton() {
  const turns = useAppStore((s) => s.turns)
  const spend = useAppStore((s) => s.session.spend)
  const active = useAppStore(selectActiveTurn)
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
  return (
    <Button
      variant="outline"
      disabled={!canOpen}
      // The session total is information even before there is a tree to open: keep it readable (5.7:1).
      className="disabled:text-muted-foreground disabled:opacity-100"
      data-trace-opener
      onClick={() => actions().setTraceOpen(true)}
      title={
        canOpen
          ? "Open the reasoning tree for the answer on the plan"
          : "Ask a question to see its reasoning"
      }
      aria-label={`Session: ${callsText(calls, "en")}, ${fmtUsd(cost)}. Open reasoning tree`}
    >
      <span className="num text-xs">
        {callsText(calls, "en")} · {fmtUsd(cost)}
      </span>
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
            aria-label="New conversation"
            disabled={!hasTurns}
            onClick={() => actions().newConversation()}
          >
            <SquarePenIcon />
          </Button>
        </TooltipTrigger>
        <TooltipContent>New conversation</TooltipContent>
      </Tooltip>
      <SessionButton />
      <Button variant="outline" onClick={() => actions().setPluginsOpen(true)}>
        <BlocksIcon data-icon="inline-start" />
        {tu("plugins")}
      </Button>
      {pluginsUsed ? (
        <Suspense fallback={null}>
          <PluginsSheet />
        </Suspense>
      ) : null}
    </>
  )
}

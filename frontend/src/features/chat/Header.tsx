import { useMemo } from 'react'
import { BlocksIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { actions, selectActiveTurn, selectDrawing, useAppStore } from '@/store'
import { PluginsSheet } from '@/features/plugins/PluginsSheet'
import { callsText, fmtUsd, roundCost } from './view'

export function DrawingTitle() {
  const d = useAppStore(selectDrawing)
  return (
    <span className="flex min-w-0 items-baseline gap-2">
      <span className="truncate font-medium text-foreground">{d.title}</span>
      {d.subtitle ? <span className="num hidden truncate text-xs text-muted-foreground sm:inline">{d.subtitle}</span> : null}
    </span>
  )
}

function SessionButton() {
  const turns = useAppStore((s) => s.turns)
  const active = useAppStore(selectActiveTurn)
  const { calls, cost } = useMemo(() => {
    let calls = 0
    let cost = 0
    for (const t of turns)
      if (t.role === 'agent' && t.response) {
        // The answer's own calls plus its check's requests (contract 1.1 reports them separately).
        calls += t.response.calls + (t.response.check?.calls ?? 0)
        cost += roundCost(t.response.cost_usd) + roundCost(t.response.check?.cost_usd ?? 0)
      }
    return { calls, cost }
  }, [turns])
  const canOpen = !!active?.response
  return (
    <Button
      variant="outline"
      disabled={!canOpen}
      data-trace-opener
      onClick={() => actions().setTraceOpen(true)}
      title={canOpen ? 'Open the reasoning tree for the answer on the plan' : 'Ask a question to see its reasoning'}
      aria-label={`Session: ${callsText(calls, 'en')}, ${fmtUsd(cost)}. Open reasoning tree`}
    >
      <span className="num text-xs">
        {callsText(calls, 'en')} · {fmtUsd(cost)}
      </span>
    </Button>
  )
}

export function HeaderActions() {
  return (
    <>
      <SessionButton />
      <Button variant="outline" onClick={() => actions().setPluginsOpen(true)}>
        <BlocksIcon data-icon="inline-start" />
        Plugins
      </Button>
      <PluginsSheet />
    </>
  )
}

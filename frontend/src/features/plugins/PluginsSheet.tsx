import { useState } from 'react'
import { useUi } from '@/i18n/uiLang'
import type { UiKey } from '@/i18n/strings'
import { CheckIcon, CopyIcon, DownloadIcon, XIcon } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import { actions, selectActiveTurn, selectDrawing, useAppStore } from '@/store'
import { PROMPT_BLOCKS, fillBlock } from '@/features/chat/draft'
import { downloadText, evidenceCsv } from './csv'

type State = 'ready' | 'backend' | 'planned'

const STATE_LABEL: Record<State, UiKey> = { ready: 'pl_ready', backend: 'pl_backend', planned: 'pl_planned' }
const STATE_STYLE: Record<State, string> = {
  ready: 'bg-success-surface text-success',
  backend: 'border border-border text-muted-foreground',
  planned: 'border border-border text-muted-foreground',
}

const MCP_SNIPPET = `{
  "mcpServers": {
    "aura": { "url": "https://<your-backend>/mcp" }
  }
}`

const REST_SNIPPET = `curl -X POST https://<your-backend>/ask \\
  -H 'content-type: application/json' \\
  -d '{ "drawing_id": "swiss-46639",
        "question": "Montako ikkunaa?" }'`

function CopyButton({ text, label }: { text: string; label: string }) {
  const { tu } = useUi()
  const [done, setDone] = useState(false)
  return (
    <Button
      variant="outline"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text)
          setDone(true)
          setTimeout(() => setDone(false), 1500)
        } catch {
          toast(tu('pl_copy_failed'))
        }
      }}
    >
      {done ? <CheckIcon data-icon="inline-start" /> : <CopyIcon data-icon="inline-start" />}
      {done ? tu('pl_copied') : label}
    </Button>
  )
}

function Card({ name, state, desc, children }: { name: string; state: State; desc: string; children?: React.ReactNode }) {
  const { tu } = useUi()
  return (
    <section className="flex min-w-0 flex-col gap-2.5 rounded-xl border bg-card p-4" aria-label={name}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{name}</h3>
        <span className={cn('rounded-full px-2 py-0.5 text-xs font-semibold', STATE_STYLE[state])}>{tu(STATE_LABEL[state])}</span>
      </div>
      <p className="text-sm text-muted-foreground">{desc}</p>
      {children}
    </section>
  )
}

function Code({ children }: { children: string }) {
  return (
    <pre className="num overflow-x-auto rounded-lg bg-primary px-3 py-2.5 text-xs leading-relaxed text-primary-foreground">
      <code>{children}</code>
    </pre>
  )
}

export function PluginsSheet() {
  const { tu, tn } = useUi()
  const open = useAppStore((s) => s.ui.pluginsOpen)
  const turn = useAppStore(selectActiveTurn)
  const drawing = useAppStore(selectDrawing)
  const resp = turn?.response
  const evidenceCount = resp ? resp.evidence.boxes.length + resp.evidence.rooms.length : 0

  const download = () => {
    if (!turn || !resp) return
    downloadText(`aura-${drawing.id}-${turn.id}-evidence.csv`, evidenceCsv(resp, turn.toggles, drawing))
  }

  return (
    <Sheet open={open} onOpenChange={(o) => actions().setPluginsOpen(o)}>
      <SheetContent side="right" showCloseButton={false} className="w-full gap-0 p-0 data-[side=right]:sm:max-w-xl">
        <SheetHeader className="flex-row items-start gap-3 border-b">
          <div className="flex flex-1 flex-col gap-0.5">
            <SheetTitle>{tu('plugins_title')}</SheetTitle>
            <SheetDescription>{tu('plugins_desc')}</SheetDescription>
          </div>
          <SheetClose asChild>
            <Button variant="ghost" size="icon" aria-label={tu('close_plugins')}>
              <XIcon />
            </Button>
          </SheetClose>
        </SheetHeader>
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
          <Card
            name="Excel / CSV"
            state="ready"
            desc={tu('pl_csv_desc')}
          >
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={download} disabled={!evidenceCount}>
                <DownloadIcon data-icon="inline-start" />
                {tu('pl_csv_download')}
              </Button>
              <span className="text-xs text-muted-foreground">
                {evidenceCount ? tn('pl_csv_rows', evidenceCount) : tu('pl_csv_empty')}
              </span>
            </div>
          </Card>
          <Card
            name={tu('pl_mcp_name')}
            state="backend"
            desc={tu('pl_mcp_desc')}
          >
            <Code>{MCP_SNIPPET}</Code>
            <div>
              <CopyButton text={MCP_SNIPPET} label={tu('pl_mcp_copy')} />
            </div>
          </Card>
          <Card
            name={tu('pl_rest_name')}
            state="backend"
            desc={tu('pl_rest_desc')}
          >
            <Code>{REST_SNIPPET}</Code>
            <div>
              <CopyButton text={REST_SNIPPET} label={tu('pl_rest_copy')} />
            </div>
          </Card>
          <Card name={tu('pl_bots_name')} state="planned" desc={tu('pl_bots_desc')} />
          <section aria-label={tu('pl_prompts_title')} className="flex flex-col gap-2 rounded-xl border border-dashed p-4">
            <h3 className="text-sm font-semibold">{tu('pl_prompts_title')}</h3>
            <p className="text-sm text-muted-foreground">{tu('pl_prompts_desc')}</p>
            <ul className="flex flex-wrap gap-1.5">
              {PROMPT_BLOCKS.map((b) => (
                <li key={b.label}>
                  <SheetClose asChild>
                    <button
                      type="button"
                      title={b.text}
                      onClick={() => fillBlock(b)}
                      className="inline-flex min-h-10 items-center rounded-lg border border-info/25 bg-info-surface px-3 text-xs font-semibold text-info outline-none hover:border-info/50 focus-visible:ring-3 focus-visible:ring-ring/50"
                    >
                      {b.label}
                    </button>
                  </SheetClose>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </SheetContent>
    </Sheet>
  )
}

import { useState } from 'react'
import { CheckIcon, CopyIcon, DownloadIcon, XIcon } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import { actions, selectActiveTurn, selectDrawing, useAppStore } from '@/store'
import { PROMPT_BLOCKS, fillBlock } from '@/features/chat/draft'
import { downloadText, evidenceCsv } from './csv'

type State = 'ready' | 'backend' | 'planned'

const STATE_LABEL: Record<State, string> = { ready: 'Ready', backend: 'Backend', planned: 'Planned' }
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
          toast('Copy failed. Select the text and copy it by hand.')
        }
      }}
    >
      {done ? <CheckIcon data-icon="inline-start" /> : <CopyIcon data-icon="inline-start" />}
      {done ? 'Copied' : label}
    </Button>
  )
}

function Card({ name, state, desc, children }: { name: string; state: State; desc: string; children?: React.ReactNode }) {
  return (
    <section className="flex min-w-0 flex-col gap-2.5 rounded-xl border bg-card p-4" aria-label={name}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{name}</h3>
        <span className={cn('rounded-full px-2 py-0.5 text-xs font-semibold', STATE_STYLE[state])}>{STATE_LABEL[state]}</span>
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
            <SheetTitle>Use it where you already work</SheetTitle>
            <SheetDescription>Same answers and evidence, wherever the question comes from.</SheetDescription>
          </div>
          <SheetClose asChild>
            <Button variant="ghost" size="icon" aria-label="Close plugins">
              <XIcon />
            </Button>
          </SheetClose>
        </SheetHeader>
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
          <Card
            name="Excel / CSV"
            state="ready"
            desc="Every piece of evidence for the answer on the plan, with its room and position, for take-off sheets. Built in your browser."
          >
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={download} disabled={!evidenceCount}>
                <DownloadIcon data-icon="inline-start" />
                Download evidence CSV
              </Button>
              <span className="text-xs text-muted-foreground">
                {evidenceCount ? `${evidenceCount} rows · your rejects included` : 'Ask a question first'}
              </span>
            </div>
          </Card>
          <Card
            name="MCP server"
            state="backend"
            desc="Ask from Claude or any MCP client; answers come back with the evidence boxes. Runs on the AURA backend, not in this page."
          >
            <Code>{MCP_SNIPPET}</Code>
            <div>
              <CopyButton text={MCP_SNIPPET} label="Copy config" />
            </div>
          </Card>
          <Card
            name="REST API"
            state="backend"
            desc="One call: drawing and question in, answer and evidence JSON out (contract 1.0). Runs on the AURA backend."
          >
            <Code>{REST_SNIPPET}</Code>
            <div>
              <CopyButton text={REST_SNIPPET} label="Copy request" />
            </div>
          </Card>
          <Card name="Chat bots" state="planned" desc="Ask from the site team’s chat: a photo of a sheet in, a marked-up answer back." />
          <section aria-label="Prompt blocks" className="flex flex-col gap-2 rounded-xl border border-dashed p-4">
            <h3 className="text-sm font-semibold">Prompt blocks</h3>
            <p className="text-sm text-muted-foreground">
              Ready-made questions this drawing can answer. Pick one to put it in the composer and edit the highlighted part.
            </p>
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

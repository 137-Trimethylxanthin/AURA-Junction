import { Link } from 'react-router'
import type { DrawingKind, DrawingSummary } from '@/api/contract'
import { apiUrl } from '@/api/library'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/lib/utils'

export const KIND_LABEL: Record<DrawingKind, string> = {
  cad_dxf: 'CAD · DXF',
  raster: 'Image',
  ifc: 'IFC',
}

const nf = new Intl.NumberFormat('en-US')

/** "38/38": only from measured facts; nothing when the API sends none. */
export function factsRatio(d: Pick<DrawingSummary, 'facts'>): string | null {
  const f = d.facts
  if (!f || f.gold_total <= 0) return null
  return `${nf.format(f.correct)}/${nf.format(f.gold_total)}`
}

export function DrawingCard({ d, offline }: { d: DrawingSummary; offline?: boolean }) {
  const ready = d.status === 'ready'
  const facts = factsRatio(d)
  const pct = d.progress !== undefined ? Math.round(Math.min(1, Math.max(0, d.progress)) * 100) : null
  const body = (
    <>
      <div className="relative aspect-[3/2] w-full overflow-hidden border-b bg-muted/60">
        {d.thumbnail ? (
          <img
            src={apiUrl(d.thumbnail)}
            alt=""
            loading="lazy"
            decoding="async"
            className="size-full object-contain p-3 mix-blend-multiply"
          />
        ) : null}
      </div>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="truncate text-sm font-medium text-foreground">{d.title}</h3>
            {d.subtitle ? <p className="num truncate text-xs text-muted-foreground">{d.subtitle}</p> : null}
          </div>
          <Badge variant="outline" className="shrink-0">
            {KIND_LABEL[d.kind]}
          </Badge>
        </div>
        {offline ? (
          <p className="text-xs text-muted-foreground">Bundled sample · answers computed in your browser</p>
        ) : null}
        {facts ? (
          <p className="text-xs text-foreground" data-facts>
            <span className="num">{facts}</span> dataset answers correct
          </p>
        ) : null}
        {!ready ? (
          d.status === 'failed' ? (
            <p className="text-xs text-destructive">Could not be read{d.error ? `: ${d.error}` : ''}</p>
          ) : (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span>{d.status === 'queued' ? 'Queued' : 'Processing'}</span>
              {pct !== null ? <Progress value={pct} aria-label={`${d.title}: processing`} className="flex-1" /> : null}
            </div>
          )
        ) : null}
        {/* The licence leads, so the CC BY credit stays visible when the text is clamped. */}
        <p className="mt-auto line-clamp-2 text-xs text-muted-foreground" title={d.credit.text} data-credit>
          <span className="text-foreground">{d.credit.license}</span> · {d.credit.text}
        </p>
      </div>
    </>
  )
  const frame =
    'group/drawing flex h-full flex-col overflow-hidden rounded-xl bg-card text-card-foreground ring-1 ring-foreground/10'
  if (!ready) return <article className={frame} data-drawing-card={d.id}>{body}</article>
  return (
    <article className="h-full" data-drawing-card={d.id}>
      <Link
        to={`/d/${encodeURIComponent(d.id)}`}
        aria-label={`Open ${d.title}${d.subtitle ? `, ${d.subtitle}` : ''}`}
        className={cn(frame, 'transition-shadow [@media(hover:hover)]:hover:ring-foreground/30')}
      >
        {body}
      </Link>
    </article>
  )
}

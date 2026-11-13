import { Link } from 'react-router'
import type { DrawingKind, DrawingSummary } from '@/api/contract'
import { apiUrl } from '@/api/library'
import { Badge } from '@/components/ui/badge'
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'

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

function CardBody({ d, offline }: { d: DrawingSummary; offline?: boolean }) {
  const ready = d.status === 'ready'
  const facts = factsRatio(d)
  const pct = d.progress !== undefined ? Math.round(Math.min(1, Math.max(0, d.progress)) * 100) : null
  return (
    <Card size="sm" className="h-full gap-3 pt-0">
      <div className="relative aspect-[3/2] w-full overflow-hidden border-b bg-muted/60">
        {d.thumbnail ? (
          <img
            src={apiUrl(d.thumbnail)}
            alt=""
            loading="lazy"
            decoding="async"
            className="size-full object-contain p-3 mix-blend-multiply transition-transform duration-300 ease-out [@media(hover:hover)]:group-hover/drawing:scale-[1.02] motion-reduce:transition-none"
          />
        ) : null}
      </div>
      <CardHeader>
        <CardTitle>
          <h3 className="truncate">{d.title}</h3>
        </CardTitle>
        {d.subtitle ? <CardDescription className="num truncate text-xs">{d.subtitle}</CardDescription> : null}
        <CardAction>
          <Badge variant="outline">{KIND_LABEL[d.kind]}</Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-1.5 text-xs">
        {offline ? <p className="text-muted-foreground">Bundled sample · answers computed in your browser</p> : null}
        {facts ? (
          <p className="text-foreground" data-facts>
            <span className="num">{facts}</span> dataset answers correct
          </p>
        ) : null}
        {!ready ? (
          d.status === 'failed' ? (
            <p className="text-destructive">Could not be read{d.error ? `: ${d.error}` : ''}</p>
          ) : (
            <div className="flex items-center gap-2 text-muted-foreground">
              <span>{d.status === 'queued' ? 'Queued' : 'Processing'}</span>
              {pct !== null ? <Progress value={pct} aria-label={`${d.title}: processing`} className="flex-1" /> : null}
            </div>
          )
        ) : null}
      </CardContent>
      <CardFooter className="mt-auto border-t-0 bg-transparent pt-0">
        {/* The licence leads, so the CC BY credit stays visible when the text is clamped. */}
        <p className="line-clamp-2 text-xs text-muted-foreground" title={d.credit.text} data-credit>
          <span className="text-foreground">{d.credit.license}</span> · {d.credit.text}
        </p>
      </CardFooter>
    </Card>
  )
}

export function DrawingCard({ d, offline }: { d: DrawingSummary; offline?: boolean }) {
  if (d.status !== 'ready')
    return (
      <article className="h-full" data-drawing-card={d.id}>
        <CardBody d={d} offline={offline} />
      </article>
    )
  return (
    <article className="h-full" data-drawing-card={d.id}>
      {/* Press feedback on pointer-down (scale), a gentle lift for fine pointers only. */}
      <Link
        to={`/d/${encodeURIComponent(d.id)}`}
        aria-label={`Open ${d.title}${d.subtitle ? `, ${d.subtitle}` : ''}`}
        className="group/drawing block h-full rounded-xl transition-transform duration-200 ease-out active:scale-[0.985] active:duration-75 motion-reduce:transition-none [@media(hover:hover)]:hover:-translate-y-0.5"
      >
        <CardBody d={d} offline={offline} />
      </Link>
    </article>
  )
}

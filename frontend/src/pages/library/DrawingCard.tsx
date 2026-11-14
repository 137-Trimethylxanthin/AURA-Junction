import { useState } from "react"
import { Link } from "react-router"
import { FileIcon } from "lucide-react"
import { licenceLabel, type DrawingKind, type DrawingSummary } from "@/api/contract"
import { apiUrl } from "@/api/library"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { useUi } from "@/i18n/uiLang"

export const KIND_LABEL: Record<DrawingKind, string> = {
  cad_dxf: "CAD · DXF",
  raster: "Image",
  ifc: "IFC",
}

/** The kind in the interface language ("Image" is the only word in it). */
export const kindText = (k: DrawingKind, tu: (key: "kind_image") => string) => (k === "raster" ? tu("kind_image") : KIND_LABEL[k])

const nf = new Intl.NumberFormat("en-US")

/** "38/38": only from measured facts; nothing when the API sends none. */
export function factsRatio(d: Pick<DrawingSummary, "facts">): string | null {
  const f = d.facts
  if (!f || f.gold_total <= 0) return null
  return `${nf.format(f.correct)}/${nf.format(f.gold_total)}`
}

function CardBody({ d, offline }: { d: DrawingSummary; offline?: boolean }) {
  const { tu } = useUi()
  const kindLabel = kindText(d.kind, tu)
  const ready = d.status === "ready"
  const facts = factsRatio(d)
  const [broken, setBroken] = useState(false)
  const thumb = d.thumbnail ? apiUrl(d.thumbnail) : null
  const pct =
    d.progress !== undefined
      ? Math.round(Math.min(1, Math.max(0, d.progress)) * 100)
      : null
  return (
    <Card size="sm" className="h-full gap-3 pt-0">
      <div className="relative aspect-[3/2] w-full overflow-hidden border-b bg-muted/60">
        {thumb && !broken ? (
          <img
            src={thumb}
            alt=""
            loading="lazy"
            decoding="async"
            onError={() => setBroken(true)}
            className="size-full object-contain p-3 mix-blend-multiply transition-transform duration-300 ease-out motion-reduce:transition-none [@media(hover:hover)]:group-hover/drawing:scale-[1.02]"
          />
        ) : (
          // No preview (missing or failed to load): a quiet placeholder with the kind, never a broken image.
          <div className="flex size-full flex-col items-center justify-center gap-1.5 text-muted-foreground" data-no-thumbnail>
            <FileIcon className="size-6" aria-hidden />
            <span className="text-xs">{tu("card_no_preview", { kind: kindLabel })}</span>
          </div>
        )}
      </div>
      <CardHeader>
        <CardTitle className="min-w-0">
          <h3 className="truncate" title={d.title}>
            {d.title}
          </h3>
        </CardTitle>
        {d.subtitle ? (
          <CardDescription className="num min-w-0 truncate text-xs" title={d.subtitle}>
            {d.subtitle}
          </CardDescription>
        ) : null}
        <CardAction>
          <Badge variant="outline">{kindLabel}</Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-1.5 text-xs">
        {offline ? (
          <p className="text-muted-foreground">{tu("card_bundled")}</p>
        ) : null}
        {facts ? (
          <p className="text-foreground" data-facts>
            <span className="num">{facts}</span> {tu("card_facts")}
          </p>
        ) : null}
        {!ready ? (
          d.status === "failed" ? (
            <p
              className="line-clamp-2 text-destructive [overflow-wrap:anywhere]"
              title={d.error}
            >
              {tu("phase_failed")}
              {d.error ? `: ${d.error}` : ""}
            </p>
          ) : (
            <div className="flex items-center gap-2 text-muted-foreground">
              <span>{d.status === "queued" ? tu("phase_queued") : tu("phase_processing")}</span>
              {pct !== null ? (
                <Progress
                  value={pct}
                  aria-label={tu("card_processing_aria", { title: d.title })}
                  className="flex-1"
                />
              ) : null}
            </div>
          )
        ) : null}
      </CardContent>
      <CardFooter className="mt-auto border-t-0 bg-transparent pt-0">
        {/* The licence leads, so the CC BY credit stays visible when the text is clamped. */}
        <p
          className="line-clamp-2 text-xs text-muted-foreground"
          title={d.credit.text}
          data-credit
        >
          <span className="text-foreground">{licenceLabel(d.credit)}</span> ·{" "}
          {d.credit.text}
        </p>
      </CardFooter>
    </Card>
  )
}

export function DrawingCard({
  d,
  offline,
}: {
  d: DrawingSummary
  offline?: boolean
}) {
  const { tu } = useUi()
  if (d.status !== "ready")
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
        aria-label={d.subtitle ? tu("card_open_sub", { title: d.title, subtitle: d.subtitle }) : tu("card_open", { title: d.title })}
        className="group/drawing block h-full rounded-xl transition-transform duration-200 ease-out active:scale-[0.985] active:duration-75 motion-reduce:transition-none [@media(hover:hover)]:hover:-translate-y-0.5"
      >
        <CardBody d={d} offline={offline} />
      </Link>
    </article>
  )
}

import { useState } from "react"
import { Link } from "react-router"
import { CheckIcon, FileIcon } from "lucide-react"
import type { DrawingSummary } from "@/api/contract"
import {
  apiUrl,
  compactCredit,
  displayDrawingTitle,
  factsRatio,
  kindText,
} from "@/api/library"
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
import { cn } from "@/lib/utils"

function CardBody({
  d,
  offline,
  selectionMode,
  selectedOrder,
  floorLabel,
}: {
  d: DrawingSummary
  offline?: boolean
  selectionMode?: boolean
  selectedOrder?: number | null
  floorLabel?: string | null
}) {
  const { tu } = useUi()
  const title = displayDrawingTitle(d)
  const sourceExt = (d as DrawingSummary & { source_ext?: string }).source_ext
  const kindLabel = kindText(d.kind, tu, sourceExt)
  const ready = d.status === "ready"
  const facts = factsRatio(d)
  const [broken, setBroken] = useState(false)
  const thumb = d.thumbnail ? apiUrl(d.thumbnail) : null
  const pct =
    d.progress !== undefined
      ? Math.round(Math.min(1, Math.max(0, d.progress)) * 100)
      : null
  const selected = selectedOrder !== null && selectedOrder !== undefined
  const compact = compactCredit(d.credit)
  const sepIdx = compact.indexOf(" · ")
  const licHead = sepIdx >= 0 ? compact.slice(0, sepIdx) : compact
  const creditRest = sepIdx >= 0 ? compact.slice(sepIdx + 3) : ""

  return (
    <Card
      size="sm"
      className={cn(
        "h-full gap-3 pt-0 transition-shadow duration-150",
        selected && "ring-2 ring-user"
      )}
    >
      <div className="relative aspect-[3/2] w-full overflow-hidden border-b bg-muted/60">
        {thumb && !broken ? (
          <img
            src={thumb}
            alt=""
            loading="lazy"
            decoding="async"
            onError={() => setBroken(true)}
            className="size-full object-contain p-3 mix-blend-multiply transition-transform duration-300 ease-out motion-reduce:transition-none [@media(hover:hover)_and_(pointer:fine)]:group-hover/drawing:scale-[1.02]"
          />
        ) : (
          // No preview (missing or failed to load): a quiet placeholder with the kind, never a broken image.
          <div
            className="flex size-full flex-col items-center justify-center gap-1.5 text-muted-foreground"
            data-no-thumbnail
          >
            <FileIcon className="size-6" aria-hidden />
            <span className="text-xs">
              {tu("card_no_preview", { kind: kindLabel })}
            </span>
          </div>
        )}
        {selectionMode && ready ? (
          <span
            aria-hidden
            className={cn(
              "num absolute top-2.5 left-2.5 inline-flex size-6 items-center justify-center rounded-full text-xs font-semibold shadow-xs transition-colors",
              selected
                ? "bg-user text-primary-foreground ring-2 ring-background"
                : "border border-input bg-background/90 text-muted-foreground"
            )}
          >
            {selected ? selectedOrder : <CheckIcon className="size-3.5 opacity-0" />}
          </span>
        ) : floorLabel ? (
          <Badge
            variant="secondary"
            className="num absolute top-2.5 left-2.5 bg-background/90 text-foreground shadow-xs"
          >
            {floorLabel}
          </Badge>
        ) : null}
      </div>
      <CardHeader>
        <CardTitle className="min-w-0">
          <h3 className="truncate" title={title}>
            {title}
          </h3>
        </CardTitle>
        {d.subtitle ? (
          <CardDescription
            className="num min-w-0 truncate text-xs"
            title={d.subtitle}
          >
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
              <span>
                {d.status === "queued"
                  ? tu("phase_queued")
                  : tu("phase_processing")}
              </span>
              {pct !== null ? (
                <Progress
                  value={pct}
                  aria-label={tu("card_processing_aria", { title })}
                  className="flex-1"
                />
              ) : null}
            </div>
          )
        ) : null}
      </CardContent>
      <CardFooter className="mt-auto border-t-0 bg-transparent pt-0">
        {/* Compact 1-line attribution keeping the licence badge visible; full dataset credit sits in the page footnote and tooltip. */}
        <p
          className="min-w-0 truncate text-xs text-muted-foreground"
          title={d.credit.text}
          data-credit
        >
          <span className="text-foreground">{licHead}</span>
          {creditRest ? ` · ${creditRest}` : ""}
        </p>
      </CardFooter>
    </Card>
  )
}

export function DrawingCard({
  d,
  offline,
  selectionMode = false,
  selectedOrder = null,
  floorLabel = null,
  onToggleSelect,
}: {
  d: DrawingSummary
  offline?: boolean
  selectionMode?: boolean
  selectedOrder?: number | null
  floorLabel?: string | null
  onToggleSelect?: (d: DrawingSummary) => void
}) {
  const { tu } = useUi()
  const title = displayDrawingTitle(d)
  if (d.status !== "ready")
    return (
      <article className="h-full" data-drawing-card={d.id}>
        <CardBody d={d} offline={offline} floorLabel={floorLabel} />
      </article>
    )
  if (selectionMode) {
    const selected = selectedOrder !== null && selectedOrder !== undefined
    return (
      <article className="h-full" data-drawing-card={d.id}>
        <button
          type="button"
          aria-pressed={selected}
          aria-label={
            d.subtitle ? `${title}, ${d.subtitle}` : title
          }
          onClick={() => onToggleSelect?.(d)}
          className="group/drawing block size-full rounded-xl text-left transition-transform duration-200 ease-out outline-none focus-visible:ring-3 focus-visible:ring-ring active:scale-[0.985] active:duration-75 motion-reduce:transition-none [@media(hover:hover)_and_(pointer:fine)]:hover:-translate-y-0.5"
        >
          <CardBody
            d={d}
            offline={offline}
            selectionMode
            selectedOrder={selectedOrder}
            floorLabel={floorLabel}
          />
        </button>
      </article>
    )
  }
  return (
    <article className="h-full" data-drawing-card={d.id}>
      {/* Press feedback on pointer-down (scale), a gentle lift for fine pointers only. */}
      <Link
        to={`/d/${encodeURIComponent(d.id)}`}
        aria-label={
          d.subtitle
            ? tu("card_open_sub", { title, subtitle: d.subtitle })
            : tu("card_open", { title })
        }
        className="group/drawing block h-full rounded-xl transition-transform duration-200 ease-out active:scale-[0.985] active:duration-75 motion-reduce:transition-none [@media(hover:hover)_and_(pointer:fine)]:hover:-translate-y-0.5"
      >
        <CardBody d={d} offline={offline} floorLabel={floorLabel} />
      </Link>
    </article>
  )
}

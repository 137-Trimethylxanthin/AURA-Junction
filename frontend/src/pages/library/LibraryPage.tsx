import { useCallback, useEffect, useMemo, useState } from "react"
import {
  RotateCwIcon,
  SearchIcon,
  SearchXIcon,
  TriangleAlertIcon,
} from "lucide-react"
import { listDrawings } from "@/api/client"
import type { DrawingKind, DrawingSummary } from "@/api/contract"
import { apiBase } from "@/api/library"
import { SiteHeader } from "@/components/shell/SiteHeader"
import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { BUNDLED_SUMMARY } from "@/data/ids"
import { useTitle } from "@/hooks/use-title"
import { factById, fmtInt } from "@/pages/landing/facts"
import { DrawingCard, KIND_LABEL } from "./DrawingCard"
import { UploadPanel } from "./UploadPanel"

type ListState =
  | { state: "loading" }
  | { state: "ready"; drawings: DrawingSummary[] }
  | { state: "error"; message: string }

/** Library drawings come from GET /drawings; without an API only the bundled sample is listed (and says so). */
function useLibrary(base: string | null) {
  const [list, setList] = useState<ListState>(() =>
    base
      ? { state: "loading" }
      : { state: "ready", drawings: [BUNDLED_SUMMARY] }
  )
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!base) return
    let live = true
    listDrawings({ baseUrl: base })
      .then(
        (r) =>
          live &&
          setList({
            state: "ready",
            drawings: r.drawings.filter((d) => d.source !== "upload"),
          })
      )
      .catch(
        (e: unknown) =>
          live &&
          setList({
            state: "error",
            message:
              e instanceof Error ? e.message : "The list could not be loaded",
          })
      )
    return () => {
      live = false
    }
  }, [base, attempt])
  const retry = useCallback(() => {
    setList({ state: "loading" })
    setAttempt((n) => n + 1)
  }, [])
  return { list, retry }
}

/** The measured full-set context line, from the facts file (never computed here). */
function FullSetLine() {
  const f = factById("swiss-full") ?? factById("swiss-dxf-accuracy")
  if (!f || f.numerator === undefined || f.denominator === undefined)
    return null
  return (
    <p className="text-xs text-muted-foreground" data-full-set>
      For context, on the full Swiss set:{" "}
      <span className="num text-foreground">{f.value}</span> of{" "}
      <span className="num">{fmtInt(f.denominator)}</span> {f.n_unit} correct (
      <span className="num">
        {fmtInt(f.numerator)}/{fmtInt(f.denominator)}
      </span>
      , measured <span className="num">{f.measured}</span>).
    </p>
  )
}

export default function LibraryPage() {
  useTitle("Drawings")
  const base = apiBase()
  const { list, retry } = useLibrary(base)
  const [query, setQuery] = useState("")
  const [kind, setKind] = useState<"all" | DrawingKind>("all")

  // A failed list still offers the bundled sample, which works without the server.
  const drawings = useMemo(
    () =>
      list.state === "ready"
        ? list.drawings
        : list.state === "error"
          ? [BUNDLED_SUMMARY]
          : [],
    [list]
  )
  const kinds = useMemo(
    () => [...new Set(drawings.map((d) => d.kind))],
    [drawings]
  )
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return drawings.filter(
      (d) =>
        (kind === "all" || d.kind === kind) &&
        (!q ||
          d.title.toLowerCase().includes(q) ||
          (d.subtitle ?? "").toLowerCase().includes(q) ||
          d.id.includes(q))
    )
  }, [drawings, query, kind])
  const anyFacts = drawings.some((d) => d.facts)
  const offline = !base || list.state === "error"

  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <SiteHeader />
      <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
        <div className="flex flex-col gap-1">
          <h1 className="text-xl font-semibold tracking-tight">Drawings</h1>
          <p className="max-w-prose text-sm text-muted-foreground">
            Open a drawing to ask about it. Every count and area is computed
            from the drawing and shown with its evidence on the plan.
          </p>
        </div>

        <div className="grid grid-cols-1 items-start gap-6 min-[1100px]:grid-cols-[minmax(0,1fr)_22rem]">
          <section
            aria-labelledby="library-title"
            className="flex min-w-0 flex-col gap-4"
          >
            <div className="flex min-h-10 flex-wrap items-center gap-3">
              <h2 id="library-title" className="mr-auto text-sm font-medium">
                Library
                {list.state === "ready" ? (
                  <span className="num ml-2 font-normal text-muted-foreground">
                    {shown.length}
                  </span>
                ) : null}
              </h2>
              {drawings.length > 1 ? (
                <div className="relative w-full sm:w-64">
                  <SearchIcon
                    className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
                    aria-hidden
                  />
                  <Input
                    type="search"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search drawings"
                    aria-label="Search drawings"
                    className="h-10 pl-9"
                  />
                </div>
              ) : null}
              {kinds.length > 1 ? (
                <ToggleGroup
                  type="single"
                  variant="outline"
                  spacing={0}
                  value={kind}
                  onValueChange={(v) => setKind((v || "all") as typeof kind)}
                  aria-label="Drawing kind"
                >
                  <ToggleGroupItem value="all">All</ToggleGroupItem>
                  {kinds.map((k) => (
                    <ToggleGroupItem key={k} value={k}>
                      {KIND_LABEL[k]}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
              ) : null}
            </div>

            {list.state === "error" ? (
              <Alert variant="destructive" data-library-error>
                <TriangleAlertIcon />
                <AlertTitle>The drawing list could not be loaded</AlertTitle>
                <AlertDescription>
                  The server did not answer. The bundled sample below still
                  works offline.
                </AlertDescription>
                <AlertAction>
                  <Button variant="outline" onClick={retry}>
                    <RotateCwIcon data-icon="inline-start" />
                    Retry
                  </Button>
                </AlertAction>
              </Alert>
            ) : null}
            {!base ? (
              <p className="text-xs text-muted-foreground" data-offline-note>
                No server is connected to this build, so the library shows the
                bundled sample drawing only.
              </p>
            ) : null}
            {anyFacts ? <FullSetLine /> : null}

            {list.state === "loading" ? (
              <ul
                className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-4"
                aria-busy="true"
                aria-label="Loading drawings"
              >
                {Array.from({ length: 6 }, (_, i) => (
                  <li
                    key={i}
                    className="flex flex-col gap-3 overflow-hidden rounded-xl ring-1 ring-foreground/10"
                  >
                    <Skeleton className="aspect-[3/2] w-full rounded-none" />
                    <div className="flex flex-col gap-2 px-4 pb-4">
                      <Skeleton className="h-4 w-2/3" />
                      <Skeleton className="h-3 w-1/3" />
                    </div>
                  </li>
                ))}
              </ul>
            ) : shown.length ? (
              <ul
                className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-4"
                aria-label="Drawings"
              >
                {shown.map((d, i) => (
                  // Cards settle in reading order; the stagger is capped so a long library never waits.
                  <li
                    key={d.id}
                    className="enter-rise"
                    style={{ animationDelay: `${Math.min(i, 8) * 30}ms` }}
                  >
                    <DrawingCard
                      d={d}
                      offline={offline && d.id === BUNDLED_SUMMARY.id}
                    />
                  </li>
                ))}
              </ul>
            ) : (
              <Empty className="border" data-library-empty>
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <SearchXIcon />
                  </EmptyMedia>
                  <EmptyTitle>
                    {drawings.length ? "No drawing matches" : "No drawings yet"}
                  </EmptyTitle>
                  <EmptyDescription>
                    {drawings.length
                      ? "Try another search or kind."
                      : "Upload a DXF to get started."}
                  </EmptyDescription>
                </EmptyHeader>
              </Empty>
            )}
          </section>

          <aside className="order-first flex flex-col gap-4 min-[1100px]:sticky min-[1100px]:top-6 min-[1100px]:order-none min-[1100px]:-m-1 min-[1100px]:max-h-[calc(100dvh-3rem)] min-[1100px]:overflow-y-auto min-[1100px]:p-1">
            <UploadPanel base={base} />
          </aside>
        </div>
      </main>
    </div>
  )
}

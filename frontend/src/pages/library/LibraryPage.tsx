import { useUi } from "@/i18n/uiLang"
import { useCallback, useEffect, useMemo, useState } from "react"
import { Link } from "react-router"
import {
  FileUpIcon,
  LayersIcon,
  RotateCwIcon,
  SearchIcon,
  SearchXIcon,
  TriangleAlertIcon,
} from "lucide-react"
import { ApiError, listDrawings } from "@/api/client"
import type { DrawingKind, DrawingSummary, Lang } from "@/api/contract"
import {
  apiBase,
  displayDrawingTitle,
  myProjects,
} from "@/api/library"
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
import { usePrefersReducedMotion } from "@/pages/landing/springs"
import { actions } from "@/store"
import { DrawingCard } from "./DrawingCard"
import {
  ProjectBundlerBar,
  type SelectablePlan,
  type SelectionFloorMeta,
} from "./ProjectBundlerBar"
import { UploadPanel } from "./UploadPanel"
import { STEP_LABEL, reportedCounts } from "@/pages/upload/stages"
import { useStoreMode, useUploads } from "@/pages/upload/useUploads"
import { Badge } from "@/components/ui/badge"
import type { UiKey } from "@/i18n/strings"
import {
  LIBRARY_SHOWS_BUNDLED,
  fileTypeOf,
  inProgressCount,
  ownUploadEntries,
  type FileType,
  type OwnUpload,
} from "./ownUploads"

const GROUP_BTN_COPY: Record<Lang, { open: string; close: string }> = {
  en: { open: "Group plans", close: "Done grouping" },
  fr: { open: "Grouper des plans", close: "Terminer" },
  fi: { open: "Ryhmittele pohjat", close: "Valmis" },
  de: { open: "Pläne gruppieren", close: "Fertig" },
}

type ListState =
  | { state: "loading" }
  | {
      state: "ready"
      drawings: DrawingSummary[]
      groupedUploads: DrawingSummary[]
      dropped?: number
    }
  | { state: "error"; message: string; unreadable?: boolean }

/** Library drawings come from GET /drawings; without an API only the bundled sample is listed (and says so). */
function useLibrary(base: string | null) {
  const [list, setList] = useState<ListState>(() =>
    base
      ? { state: "loading" }
      : { state: "ready", drawings: [BUNDLED_SUMMARY], groupedUploads: [] }
  )
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!base) return
    let live = true
    listDrawings({ baseUrl: base })
      .then((r) => {
        if (!live) return
        setList({
          state: "ready",
          drawings: r.drawings.filter((d) => d.source !== "upload"),
          groupedUploads: r.drawings.filter(
            (d) =>
              d.source === "upload" &&
              (Boolean(d.project_id) || /^up-[a-z0-9]+-p\d+$/i.test(d.id))
          ),
          dropped: r.dropped,
        })
      })
      .catch((e: unknown) => {
        if (!live) return
        // Background refreshes keep already-loaded library cards intact on transient hiccups.
        setList((prev) =>
          prev.state === "ready"
            ? prev
            : {
                state: "error",
                message:
                  e instanceof Error ? e.message : "The list could not be loaded",
                // The server answered, but with something this app cannot read.
                unreadable: e instanceof ApiError && e.code === "invalid_response",
              }
        )
      })
    return () => {
      live = false
    }
  }, [base, attempt])

  const retry = useCallback(() => {
    setList({ state: "loading" })
    setAttempt((n) => n + 1)
  }, [])

  /** Silent background re-fetch (used when a multi-page PDF upload finishes splitting on the server). */
  const refresh = useCallback(() => {
    if (!base) return
    setAttempt((n) => n + 1)
  }, [base])

  return { list, retry, refresh }
}

/** The measured full-set context line, from the facts file (never computed here). */
function FullSetLine() {
  const { tu } = useUi()
  const f = factById("swiss-full") ?? factById("swiss-dxf-accuracy")
  if (!f || f.numerator === undefined || f.denominator === undefined)
    return null
  return (
    <p className="text-xs text-muted-foreground" data-full-set>
      <span className="num">
        {tu("lib_full_set", {
          value: f.value,
          total: fmtInt(f.denominator),
          unit: f.n_unit,
          num: fmtInt(f.numerator),
          measured: f.measured,
        })}
      </span>
    </p>
  )
}

export default function LibraryPage() {
  const { lang, tu } = useUi()
  const reducedMotion = usePrefersReducedMotion()
  useTitle(tu("nav_drawings"))
  const base = apiBase()
  const { list, retry, refresh } = useLibrary(base)
  const [query, setQuery] = useState("")
  const [kind, setKind] = useState<"all" | FileType>("all")

  const [grouping, setGrouping] = useState(false)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [selectionMeta, setSelectionMeta] = useState<
    Record<string, SelectionFloorMeta>
  >({})
  // The user's own uploads: finished ones become library cards; the rest stay on /upload.
  const mode = useStoreMode(base)
  const { rows: uploadRows, remove: removeUpload } = useUploadsOrNone(base, mode, refresh)
  const own = useMemo(
    () => ownUploadEntries(uploadRows, mode, (n) => tu("ul_page", { n })),
    [uploadRows, mode, tu]
  )
  const ownById = useMemo(() => new Map(own.map((o) => [o.summary.id, o])), [own])
  const uploading = inProgressCount(uploadRows)

  // Bundled demo drawings are listed only where the build says so (off in dev); they always open by URL.
  // A failed list still offers the bundled sample, which works without the server.
  const bundled = useMemo(
    () =>
      !LIBRARY_SHOWS_BUNDLED
        ? []
        : list.state === "ready"
          ? list.drawings
          : list.state === "error"
            ? [BUNDLED_SUMMARY]
            : [],
    [list]
  )
  const drawings = useMemo(
    () => [...own.map((o) => ({ ...o.summary, subtitle: o.summary.subtitle ?? countsLine(o, tu) })), ...bundled],
    [own, bundled, tu]
  )

  const selectablePlans = useMemo<SelectablePlan[]>(() => {
    const map = new Map<string, SelectablePlan>()
    for (const d of drawings) {
      if (d.status !== "ready") continue
      map.set(d.id, {
        id: d.id,
        title: d.title,
        subtitle: d.subtitle,
        kind: d.kind,
        credit: d.credit,
        source: d.source ?? "library",
        project_id: d.project_id,
        floor_label: d.floor_label,
        floor_order: d.floor_order,
      })
    }
    if (list.state === "ready") {
      for (const u of list.groupedUploads) {
        if (u.status !== "ready") continue
        map.set(u.id, {
          id: u.id,
          title: u.title,
          subtitle: u.subtitle,
          kind: u.kind,
          credit: u.credit,
          source: "upload",
          project_id: u.project_id,
          floor_label: u.floor_label,
          floor_order: u.floor_order,
        })
      }
    }
    return [...map.values()]
  }, [drawings, list])

  const selectableSummaries = useMemo<DrawingSummary[]>(
    () =>
      selectablePlans.map((p) => ({
        id: p.id,
        title: p.title,
        subtitle: p.subtitle,
        kind: p.kind ?? fileKind(p.title),
        status: "ready",
        source: p.source ?? "library",
        thumbnail: "",
        credit: p.credit ?? { text: p.title, license: "" },
        ...(p.project_id ? { project_id: p.project_id } : {}),
        ...(p.floor_label ? { floor_label: p.floor_label } : {}),
        ...(p.floor_order !== undefined ? { floor_order: p.floor_order } : {}),
      })),
    [selectablePlans]
  )

  const activateBundleForDrawing = useCallback(
    (drawingId: string) => {
      const bundle = myProjects.forDrawing(drawingId, selectableSummaries)
      if (bundle && bundle.floors.length >= 2) {
        myProjects.save(bundle)
        actions().setActiveProject(bundle)
      } else {
        actions().setActiveProject(null)
      }
    },
    [selectableSummaries]
  )

  const kinds = useMemo(() => {
    const order: FileType[] = ["DXF", "IFC", "PDF", "PNG", "JPG", "SVG", "WEBP", "IMAGE"]
    const present = new Set(drawings.map((d) => fileTypeOf(d)))
    return order
      .filter((t) => present.has(t))
      .map((t) => ({ t, n: drawings.filter((d) => fileTypeOf(d) === t).length }))
  }, [drawings])
  // Sheets of a project are shown by the project card, not again one by one (except while grouping).
  const [projectsVersion, setProjectsVersion] = useState(0)
  useEffect(() => myProjects.subscribe(() => setProjectsVersion((n) => n + 1)), [])
  const inProject = useMemo(() => {
    void projectsVersion
    return new Set(myProjects.resolveAll(drawings).flatMap((p) => p.floors.map((f) => f.drawing_id)))
  }, [drawings, projectsVersion])
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return drawings.filter((d) => {
      if (!grouping && inProject.has(d.id)) return false
      if (kind !== "all" && fileTypeOf(d) !== kind) return false
      if (!q) return true
      const display = displayDrawingTitle(d).toLowerCase()
      return (
        display.includes(q) ||
        d.title.toLowerCase().includes(q) ||
        (d.subtitle ?? "").toLowerCase().includes(q) ||
        d.id.toLowerCase().includes(q)
      )
    })
  }, [drawings, query, kind, grouping, inProject])
  const anyFacts = drawings.some((d) => d.facts)
  const offline = !base || list.state === "error"

  const toggleSelectDrawing = useCallback((d: DrawingSummary) => {
    setSelectedIds((prev) =>
      prev.includes(d.id) ? prev.filter((x) => x !== d.id) : [...prev, d.id]
    )
  }, [])

  const canGroup = selectablePlans.length >= 2 || grouping

  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <SiteHeader />
      <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
        <div className="flex flex-col gap-1">
          <h1 className="text-xl font-semibold tracking-tight">{tu("nav_drawings")}</h1>
          <p className="max-w-prose text-sm text-muted-foreground">{tu("lib_intro")}</p>
        </div>

        <div className="grid grid-cols-1 items-start gap-6 min-[1100px]:grid-cols-[minmax(0,1fr)_22rem]">
          <section
            aria-labelledby="library-title"
            className="flex min-w-0 flex-col gap-4"
          >
            <div className="flex min-h-10 flex-wrap items-center gap-3">
              <h2 id="library-title" className="mr-auto text-sm font-medium">
                {tu("library")}
                {list.state === "ready" ? (
                  <>
                    {" "}
                    <span className="num ml-1.5 font-normal text-muted-foreground">
                      {drawings.length}
                    </span>
                  </>
                ) : null}
              </h2>
              {canGroup ? (
                <Button
                  type="button"
                  variant={grouping ? "default" : "outline"}
                  aria-pressed={grouping}
                  onClick={() => {
                    if (grouping) {
                      setGrouping(false)
                      setSelectedIds([])
                    } else {
                      setGrouping(true)
                    }
                  }}
                  data-group-plans-toggle
                >
                  <LayersIcon data-icon="inline-start" />
                  {grouping ? GROUP_BTN_COPY[lang].close : GROUP_BTN_COPY[lang].open}
                </Button>
              ) : null}
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
                    placeholder={tu("search_drawings")}
                    aria-label={tu("search_drawings")}
                    className="h-10 pl-9"
                  />
                </div>
              ) : null}
              {kinds.length > 1 ? (
                <ToggleGroup
                  type="single"
                  variant="outline"
                  spacing={1}
                  value={kind}
                  onValueChange={(v) => setKind((v || "all") as typeof kind)}
                  aria-label={tu("lib_file_type")}
                  className="flex-wrap"
                  data-type-filter
                >
                  <ToggleGroupItem value="all">{tu("lib_all")}</ToggleGroupItem>
                  {kinds.map(({ t, n }) => (
                    <ToggleGroupItem key={t} value={t} aria-label={`${typeLabel(t, tu)} (${n})`}>
                      {typeLabel(t, tu)}
                      <span className="num text-muted-foreground">{n}</span>
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
              ) : null}
            </div>

            {list.state !== "loading" ? (
              <ProjectBundlerBar
                base={base}
                plans={selectablePlans}
                grouping={grouping}
                onGroupingChange={setGrouping}
                selectedIds={selectedIds}
                onSelectedIdsChange={setSelectedIds}
                onSelectionMetaChange={setSelectionMeta}
              />
            ) : null}

            {list.state === "error" ? (
              <Alert variant="destructive" data-library-error>
                <TriangleAlertIcon />
                <AlertTitle>{tu("list_failed")}</AlertTitle>
                <AlertDescription>
                  {list.unreadable ? tu("list_unreadable") : tu("list_no_answer")}{" "}
                  {tu("list_sample_works")}
                </AlertDescription>
                <AlertAction>
                  <Button variant="outline" onClick={retry}>
                    <RotateCwIcon data-icon="inline-start" />
                    {tu("retry")}
                  </Button>
                </AlertAction>
              </Alert>
            ) : null}
            {list.state === "ready" && list.dropped ? (
              <p className="text-xs text-warning" data-library-dropped>
                {list.dropped === 1 ? tu("dropped_1") : tu("dropped_n", { n: list.dropped })}
              </p>
            ) : null}
            {!base ? (
              <p className="text-xs text-muted-foreground" data-offline-note>
                {tu("lib_offline")}
              </p>
            ) : null}
            {anyFacts ? <FullSetLine /> : null}

            {list.state === "loading" ? (
              <ul
                className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-4"
                aria-busy="true"
                aria-label={tu("lib_loading")}
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
            ) : !shown.length && drawings.length && !query.trim() && kind === "all" ? null : shown.length ? (
              <ul
                className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-4"
                aria-label={tu("nav_drawings")}
              >
                {shown.map((d, i) => {
                  const meta = selectionMeta[d.id]
                  const fallbackOrder = selectedIds.indexOf(d.id)
                  const selectedOrder =
                    meta?.order ?? (fallbackOrder >= 0 ? fallbackOrder + 1 : undefined)
                  return (
                    // Cards settle in reading order; the stagger is capped so a long library never waits.
                    <li
                      key={d.id}
                      className="enter-rise"
                      style={
                        reducedMotion
                          ? undefined
                          : { animationDelay: `${Math.min(i, 8) * 30}ms` }
                      }
                    >
                      <DrawingCard
                        d={d}
                        badge={stageBadge(ownById.get(d.id), tu)}
                        onDelete={
                          ownById.get(d.id)?.deletable
                            ? () => void removeUpload(ownById.get(d.id)!.row)
                            : undefined
                        }
                        offline={offline && d.id === BUNDLED_SUMMARY.id}
                        selectionMode={grouping}
                        selectedOrder={selectedOrder}
                        floorLabel={meta?.label}
                        onToggleSelect={toggleSelectDrawing}
                        onOpen={(item) => activateBundleForDrawing(item.id)}
                      />
                    </li>
                  )
                })}
              </ul>
            ) : (
              <Empty className="border" data-library-empty>
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    {drawings.length ? <SearchXIcon /> : <FileUpIcon />}
                  </EmptyMedia>
                  <EmptyTitle>
                    {drawings.length ? tu("no_match") : tu("lib_empty_title")}
                  </EmptyTitle>
                  <EmptyDescription>
                    {drawings.length ? tu("no_match_text") : tu("lib_empty_desc")}
                  </EmptyDescription>
                </EmptyHeader>
                {drawings.length ? null : (
                  <Button asChild>
                    <Link to="/upload">{tu("ul_lib_cta")}</Link>
                  </Button>
                )}
              </Empty>
            )}

            {drawings.length > 0 ? (
              <p className="border-t pt-4 text-xs text-muted-foreground" data-library-credits>
                {tu("lib_credits_footnote")}{" "}
                <Link
                  to="/credits"
                  className="font-medium text-foreground underline underline-offset-2"
                >
                  {tu("nav_credits")}
                </Link>
              </p>
            ) : null}
          </section>

          <aside className="order-first flex flex-col gap-4 min-[1100px]:sticky min-[1100px]:top-6 min-[1100px]:order-none min-[1100px]:-m-1 min-[1100px]:max-h-[calc(100dvh-3rem)] min-[1100px]:overflow-y-auto min-[1100px]:p-1">
            <UploadPanel base={base} uploading={uploading} />
          </aside>
        </div>
      </main>
    </div>
  )
}

/** The upload hook needs an API base; without one the user has no uploads. */
function useUploadsOrNone(base: string | null, mode: "full" | "quick" | null, onProjectsDiscovered: () => void) {
  const u = useUploads(base ?? "", { mode, onProjectsDiscovered })
  return base ? u : { ...u, rows: [] }
}

const fileKind = (name: string): DrawingKind =>
  /\.ifc$/i.test(name) ? "ifc" : /\.dxf$/i.test(name) ? "cad_dxf" : "raster"

function typeLabel(t: FileType, tu: (k: UiKey) => string): string {
  return t === "IMAGE" ? tu("kind_image") : t
}

/** "13 rooms · ≈16 doors · ≈8 windows": what the upload found; "≈" marks a model estimate. */
function countsLine(o: OwnUpload, tu: (k: UiKey) => string): string | undefined {
  const parts = reportedCounts(o.counts)
    .filter((c) => c.key === "rooms" || c.key === "doors" || c.key === "windows")
    .map((c) => `${o.estimates.has(c.key) ? "≈" : ""}${c.n} ${tu(`ul_c_${c.key}` as UiKey).toLowerCase()}`)
  return parts.length ? parts.join(" · ") : undefined
}

/** The indexing stage while the store is still reading a finished upload; a warning when it stopped short. */
function stageBadge(o: OwnUpload | undefined, tu: (k: UiKey) => string) {
  if (!o) return undefined
  const v = o.view
  if (v.outcome === "working")
    return (
      <Badge variant="secondary" className="max-w-full bg-background/90 shadow-xs" data-upload-stage={v.step}>
        <span className="truncate">{tu(STEP_LABEL[v.step])}</span>
      </Badge>
    )
  if (v.outcome === "partial" || v.outcome === "store_failed" || v.outcome === "too_slow")
    return (
      <Badge variant="outline" className="max-w-full border-warning/40 bg-background/90 text-warning" data-upload-stage={v.outcome}>
        <span className="truncate">{tu(v.outcome === "partial" ? "ul_o_partial" : v.outcome === "too_slow" ? "ul_o_too_slow" : "ul_o_store_failed")}</span>
      </Badge>
    )
  return undefined
}

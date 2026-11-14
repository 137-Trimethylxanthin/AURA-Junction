import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react"
import { Link } from "react-router"
import { toast } from "sonner"
import {
  ArrowDownIcon,
  ArrowUpIcon,
  Building2Icon,
  CheckIcon,
  LayersIcon,
  PencilIcon,
  UnlinkIcon,
  XIcon,
} from "lucide-react"
import type {
  Credit,
  DrawingKind,
  DrawingSummary,
  Lang,
  ProjectBundle,
  ProjectFloor,
} from "@/api/contract"
import { deleteProject, saveProject } from "@/api/client"
import {
  displayDrawingTitle,
  inferFloorOrderAndLabel,
  myProjects,
} from "@/api/library"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { useUi } from "@/i18n/uiLang"
import { actions } from "@/store"
import { fileTypeOf } from "./ownUploads"

export interface SelectablePlan {
  id: string
  title: string
  subtitle?: string
  kind?: DrawingKind
  credit?: Credit
  source?: "library" | "upload"
  project_id?: string
  floor_label?: string
  floor_order?: number
}

export interface SelectionFloorMeta {
  order: number
  label: string
}

interface ExtraCopy {
  editProject: string
  moveUp: (label: string) => string
  moveDown: (label: string) => string
  removePlan: (title: string) => string
  uploadsPickLabel: string
}

const EXTRA_COPY: Record<Lang, ExtraCopy> = {
  en: {
    editProject: "Edit",
    moveUp: (l) => `Move ${l} up`,
    moveDown: (l) => `Move ${l} down`,
    removePlan: (t) => `Remove ${t} from project`,
    uploadsPickLabel: "Your ready uploads",
  },
  fr: {
    editProject: "Modifier",
    moveUp: (l) => `Monter ${l}`,
    moveDown: (l) => `Descendre ${l}`,
    removePlan: (t) => `Retirer ${t} du projet`,
    uploadsPickLabel: "Vos fichiers prêts",
  },
  fi: {
    editProject: "Muokkaa",
    moveUp: (l) => `Siirrä ${l} ylemmäs`,
    moveDown: (l) => `Siirrä ${l} alemmas`,
    removePlan: (t) => `Poista ${t} projektista`,
    uploadsPickLabel: "Valmiit omat lataukset",
  },
  de: {
    editProject: "Bearbeiten",
    moveUp: (l) => `${l} nach oben`,
    moveDown: (l) => `${l} nach unten`,
    removePlan: (t) => `${t} aus Projekt entfernen`,
    uploadsPickLabel: "Bereite eigene Uploads",
  },
}

const GROUND_TOKEN_RE =
  /\b(?:eg|erdgeschoss|parterre|rdc|rez[- ]de[- ]chauss[eé]e|rez|pohjakerros|piano\s+terra|gf|ground(?:\s+(?:floor|level|storey))?|(?:level|floor|storey)\s*0|0\s*\.?\s*level|0f)\b/i

const FLOOR_STRIP_RE =
  /\b([1-9]\d?\s*\.?\s*(?:ug|untergeschoss|kg|keller|kellergeschoss|og|obergeschoss|kerros|krs|level)|[1-9]\d?\s*(?:st|nd|rd|th)?\s*(?:floor|storey|story)|(?:floor|storey|story|level|lvl|page|sheet|seite|sivu|feuille)\s*\d+|[1-9]\d?\s*(?:er|ère|ere|e|ème|eme)?\s*[eé]tage|kg|ug|eg|dg|gf|rdc|b[1-9]|[0-9]\d?f|kellergeschoss|untergeschoss|erdgeschoss|obergeschoss|dachgeschoss|attika|attic|penthouse|loft|roof|rooftop|ground\s*floor|basement\s*\d*|mezzanine|zwischengeschoss|rez[- ]de[- ]chauss[eé]e|rez|sous[- ]sol|combles|toiture|pohjakerros|kellari|ullakko)\b/gi

function subscribeProjects(onStoreChange: () => void): () => void {
  const unsub = myProjects.subscribe(onStoreChange)
  if (typeof window === "undefined") return unsub
  window.addEventListener("storage", onStoreChange)
  return () => {
    unsub()
    window.removeEventListener("storage", onStoreChange)
  }
}

function getProjectsSnapshot(): string {
  try {
    const saved = globalThis.localStorage?.getItem("aura.projects") ?? ""
    const ungrouped = globalThis.localStorage?.getItem("aura.projects.ungrouped") ?? ""
    return `${saved}||${ungrouped}`
  } catch {
    return ""
  }
}

function kindFromName(name: string): DrawingKind {
  const lower = name.toLowerCase()
  if (lower.endsWith(".ifc")) return "ifc"
  if (lower.endsWith(".dxf")) return "cad_dxf"
  return "raster"
}

function toSummary(plan: SelectablePlan): DrawingSummary {
  return {
    id: plan.id,
    title: plan.title,
    subtitle: plan.subtitle,
    kind: plan.kind ?? kindFromName(plan.title),
    status: "ready",
    source: plan.source ?? "library",
    thumbnail: "",
    credit: plan.credit ?? { text: plan.title, license: "" },
    ...(plan.project_id ? { project_id: plan.project_id } : {}),
    ...(plan.floor_label ? { floor_label: plan.floor_label } : {}),
    ...(plan.floor_order !== undefined ? { floor_order: plan.floor_order } : {}),
  }
}

function inferDraftPlanFloor(
  plan: SelectablePlan | undefined,
  id: string,
  fallbackIndex: number
): { order: number; label: string; explicitOrMatched: boolean } {
  if (plan?.floor_order !== undefined && plan.floor_label?.trim()) {
    return {
      order: plan.floor_order,
      label: plan.floor_label.trim(),
      explicitOrMatched: true,
    }
  }
  if (plan?.floor_label?.trim()) {
    return {
      order: plan.floor_order ?? fallbackIndex,
      label: plan.floor_label.trim(),
      explicitOrMatched: true,
    }
  }
  const sourceText = [plan?.title, plan?.subtitle, id].filter(Boolean).join(" · ")
  const inf = inferFloorOrderAndLabel(sourceText)
  const matched =
    inf.order !== 0 || inf.label !== "GF" || GROUND_TOKEN_RE.test(sourceText)
  return { order: inf.order, label: inf.label, explicitOrMatched: matched }
}

function buildingStemOf(plan: SelectablePlan): { key: string; title: string } | null {
  const display = displayDrawingTitle({
    title: plan.title,
    id: plan.id,
    credit: plan.credit,
  })
  const stem = display
    .replace(/\.(?:dxf|ifc|pdf|png|jpe?g|webp)$/i, "")
    .replace(/\bup-[a-z0-9]+(?:-p\d+)?\b/gi, " ")
    .replace(FLOOR_STRIP_RE, " ")
    .replace(/\b(?:grundriss|floor\s*plan|floorplan|pohjapiirros|plan|pianta)\b/gi, " ")
    .replace(/[·—–\-_:()/[\]]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
  const lower = stem.toLowerCase()
  if (
    stem.length >= 3 &&
    !/^\d+$/.test(stem) &&
    !/^(?:plan|drawing|upload|scan|image|model|house|building|project|project name|untitled)$/i.test(
      lower
    )
  ) {
    const titled =
      stem === lower ? stem.replace(/\b\w/g, (c) => c.toUpperCase()) : stem
    return { key: lower, title: titled }
  }
  const siteMatch = /\bsite\s+(\d+)\b/i.exec(plan.subtitle ?? "")
  if (siteMatch) {
    return { key: `site-${siteMatch[1]}`, title: `Site ${siteMatch[1]}` }
  }
  return null
}

function syncProjectToServer(base: string | null, bundle: ProjectBundle) {
  if (!base) return
  void saveProject({ baseUrl: base }, bundle, { headers: myProjects.authHeaders(bundle) })
    .then(({ token }) => myProjects.setToken(bundle.id, token))
    .catch(() => {
      /* localStorage remains authoritative when offline or before /projects is enabled */
    })
}

/** Takes the bundle, not its id: it is already removed locally, and its stored token rides along on it. */
function deleteProjectOnServer(base: string | null, bundle: ProjectBundle) {
  if (!base) return
  void deleteProject({ baseUrl: base }, bundle.id, { headers: myProjects.authHeaders(bundle) }).catch(() => {
    /* ignore offline / 404 */
  })
}

function activateProjectInWorkspace(bundle: ProjectBundle) {
  myProjects.save(bundle)
  actions().setActiveProject(bundle)
}

function entryFloorOf(bundle: ProjectBundle): ProjectFloor {
  const sorted = [...bundle.floors].sort(
    (a, b) => a.order - b.order || a.drawing_id.localeCompare(b.drawing_id)
  )
  return (
    sorted.find((f) =>
      /^(?:eg|gf|rdc|pk|pt|ground(?:\s+floor)?|erdgeschoss)$/i.test(
        f.label.trim()
      )
    ) ??
    sorted.find((f) => f.order === 0 && sorted[0].order < 0) ??
    sorted[0]
  )
}

export interface ProjectBundlerBarProps {
  base: string | null
  plans: SelectablePlan[]
  grouping: boolean
  onGroupingChange: (open: boolean) => void
  selectedIds: string[]
  onSelectedIdsChange: (ids: string[]) => void
  onSelectionMetaChange?: (meta: Record<string, SelectionFloorMeta>) => void
}

export function ProjectBundlerBar({
  base,
  plans,
  grouping,
  onGroupingChange,
  selectedIds,
  onSelectedIdsChange,
  onSelectionMetaChange,
}: ProjectBundlerBarProps) {
  const { lang, tu } = useUi()
  const extra = EXTRA_COPY[lang]

  const projectsSnap = useSyncExternalStore(
    subscribeProjects,
    getProjectsSnapshot,
    () => ""
  )

  const [editingId, setEditingId] = useState<string | null>(null)
  const [customTitle, setCustomTitle] = useState<string | null>(null)
  const [labelOverrides, setLabelOverrides] = useState<Record<string, string>>({})
  const [manuallyReordered, setManuallyReordered] = useState(false)

  const planById = useMemo(() => new Map(plans.map((p) => [p.id, p])), [plans])
  const summaryPlans = useMemo(() => plans.map(toSummary), [plans])

  const allProjects = useMemo(() => {
    void projectsSnap
    return myProjects
      .resolveAll(summaryPlans)
      .filter(
        (p) =>
          p.floors.length >= 2 &&
          (!p.auto_matched || p.floors.some((f) => planById.has(f.drawing_id)))
      )
  }, [summaryPlans, planById, projectsSnap])

  const draftFloors = useMemo<ProjectFloor[]>(() => {
    if (!grouping || selectedIds.length === 0) return []
    const raw = selectedIds.map((id, idx) => {
      const plan = planById.get(id)
      const inf = inferDraftPlanFloor(plan, id, idx)
      return {
        drawing_id: id,
        inferredLabel: inf.label,
        inferredOrder: inf.order,
        explicitOrMatched: inf.explicitOrMatched,
        clickIndex: idx,
      }
    })
    const allUnmatched = raw.every((r) => !r.explicitOrMatched)
    const items = raw.map((r, idx) => {
      const fallbackLabel = allUnmatched ? `F${idx + 1}` : r.inferredLabel
      const label =
        labelOverrides[r.drawing_id] !== undefined
          ? labelOverrides[r.drawing_id]
          : fallbackLabel
      return {
        drawing_id: r.drawing_id,
        label,
        order: manuallyReordered || allUnmatched ? idx : r.inferredOrder,
        clickIndex: r.clickIndex,
      }
    })
    const ordered = manuallyReordered
      ? items
      : [...items].sort((a, b) => a.order - b.order || a.clickIndex - b.clickIndex)
    return ordered.map(({ drawing_id, label, order }) => ({
      drawing_id,
      label,
      order,
    }))
  }, [grouping, selectedIds, planById, labelOverrides, manuallyReordered])

  const suggestedTitle = useMemo(() => {
    if (!grouping) return ""
    const selectedPlans = selectedIds
      .map((id) => planById.get(id))
      .filter((p): p is SelectablePlan => !!p)
    if (!selectedPlans.length) return ""
    const stems = selectedPlans
      .map(buildingStemOf)
      .filter((s): s is { key: string; title: string } => !!s)
    if (stems.length > 0 && stems.every((s) => s.key === stems[0].key)) {
      return stems[0].title
    }
    if (selectedPlans.length >= 2 && stems[0]?.title) {
      return stems[0].title
    }
    return ""
  }, [grouping, selectedIds, planById])

  const displayedTitle = customTitle ?? suggestedTitle

  // Sync sorted floor numbers & labels back to `LibraryPage` so `DrawingCard` displays `1 · KG`, `2 · EG`, `3 · 1. OG`.
  useEffect(() => {
    if (!onSelectionMetaChange) return
    if (!grouping) {
      onSelectionMetaChange({})
      return
    }
    const map: Record<string, SelectionFloorMeta> = {}
    draftFloors.forEach((f, idx) => {
      map[f.drawing_id] = {
        order: idx + 1,
        label: f.label.trim() || `F${idx + 1}`,
      }
    })
    onSelectionMetaChange(map)
  }, [grouping, draftFloors, onSelectionMetaChange])

  const resetBuilder = useCallback(() => {
    setEditingId(null)
    setCustomTitle(null)
    setLabelOverrides({})
    setManuallyReordered(false)
    onSelectedIdsChange([])
    onGroupingChange(false)
  }, [onGroupingChange, onSelectedIdsChange])

  const startEdit = useCallback(
    (bundle: ProjectBundle) => {
      const sorted = [...bundle.floors].sort(
        (a, b) => a.order - b.order || a.drawing_id.localeCompare(b.drawing_id)
      )
      const overrides: Record<string, string> = {}
      for (const f of sorted) overrides[f.drawing_id] = f.label
      setEditingId(bundle.id)
      setCustomTitle(bundle.title)
      setLabelOverrides(overrides)
      setManuallyReordered(true)
      onSelectedIdsChange(sorted.map((f) => f.drawing_id))
      onGroupingChange(true)
    },
    [onGroupingChange, onSelectedIdsChange]
  )

  const handleUngroup = useCallback(
    (bundle: ProjectBundle) => {
      myProjects.remove(bundle.id)
      deleteProjectOnServer(base, bundle)
      actions().setActiveProject(null)
      if (editingId === bundle.id) resetBuilder()
      toast.success(tu("proj_ungrouped_toast", { title: bundle.title }))
    },
    [base, editingId, resetBuilder, tu]
  )

  const moveFloor = (idx: number, delta: -1 | 1) => {
    const target = idx + delta
    if (target < 0 || target >= draftFloors.length) return
    const next = [...draftFloors]
    const [moved] = next.splice(idx, 1)
    next.splice(target, 0, moved)
    setManuallyReordered(true)
    onSelectedIdsChange(next.map((f) => f.drawing_id))
  }

  const updateFloorLabel = (drawingId: string, label: string) => {
    setLabelOverrides((prev) => ({ ...prev, [drawingId]: label }))
  }

  const removeFloorFromDraft = (drawingId: string) => {
    const nextIds = draftFloors
      .filter((f) => f.drawing_id !== drawingId)
      .map((f) => f.drawing_id)
    onSelectedIdsChange(nextIds)
  }

  const toggleUploadPlan = (drawingId: string) => {
    if (selectedIds.includes(drawingId)) {
      removeFloorFromDraft(drawingId)
    } else {
      onSelectedIdsChange([...selectedIds, drawingId])
    }
  }

  const handleSave = () => {
    if (draftFloors.length < 2) return
    const cleanTitle =
      displayedTitle.trim() ||
      buildingStemOf(planById.get(draftFloors[0].drawing_id) ?? { id: "", title: "" })
        ?.title ||
      tu("proj_section_title")

    let prevOrder = Number.NEGATIVE_INFINITY
    const normalizedFloors: ProjectFloor[] = draftFloors.map((f, idx) => {
      let nextOrder = manuallyReordered ? idx : f.order
      if (!manuallyReordered && nextOrder <= prevOrder) {
        nextOrder = prevOrder + 1
      }
      prevOrder = nextOrder
      return {
        drawing_id: f.drawing_id,
        label: f.label.trim() || `F${idx + 1}`,
        order: nextOrder,
      }
    })

    const id = editingId ?? `proj-${Date.now().toString(36)}`
    const saved = myProjects.save({
      id,
      title: cleanTitle,
      floors: normalizedFloors,
      auto_matched: false,
      created_at: new Date().toISOString(),
    })
    syncProjectToServer(base, saved)
    toast.success(
      tu("proj_saved_toast", { n: normalizedFloors.length, title: cleanTitle })
    )
    resetBuilder()
  }

  const uploadPlans = useMemo(
    () => plans.filter((p) => p.source === "upload"),
    [plans]
  )

  if (!grouping && allProjects.length === 0) return null

  return (
    <div className="flex min-w-0 flex-col gap-3" data-projects-region>
      {grouping ? (
        <Card
          size="sm"
          className="border-info/40 bg-info-surface/25"
          data-project-builder
        >
          <CardHeader className="pb-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                <LayersIcon className="size-4 shrink-0 text-info" aria-hidden />
                <span>{tu("proj_group_btn")}</span>
              </CardTitle>
              <Badge variant="secondary" className="num shrink-0 text-xs">
                {tu("proj_selected_n", { n: draftFloors.length })}
              </Badge>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5 sm:max-w-sm">
              <label
                htmlFor="project-title-input"
                className="text-xs font-medium text-foreground"
              >
                {tu("proj_title_label")}
              </label>
              <Input
                id="project-title-input"
                value={displayedTitle}
                maxLength={80}
                onChange={(e) => setCustomTitle(e.target.value)}
                placeholder={tu("proj_title_placeholder")}
                className="h-10 bg-background"
              />
            </div>

            {uploadPlans.length > 0 ? (
              <div className="flex flex-col gap-1.5">
                <span className="text-xs font-medium text-muted-foreground">
                  {extra.uploadsPickLabel}
                </span>
                <div className="flex flex-wrap gap-2">
                  {uploadPlans.map((up) => {
                    const picked = selectedIds.includes(up.id)
                    return (
                      <Button
                        key={up.id}
                        type="button"
                        variant={picked ? "default" : "outline"}
                        aria-pressed={picked}
                        onClick={() => toggleUploadPlan(up.id)}
                        className="min-h-10 min-w-10 max-w-full text-xs"
                      >
                        {picked ? <CheckIcon data-icon="inline-start" /> : null}
                        <span className="max-w-48 truncate">{up.title}</span>
                      </Button>
                    )
                  })}
                </div>
              </div>
            ) : null}

            {draftFloors.length > 0 ? (
              <ol className="flex flex-col gap-2">
                {draftFloors.map((floor, idx) => {
                  const plan = planById.get(floor.drawing_id)
                  const planTitle = plan
                    ? displayDrawingTitle({
                        title: plan.title,
                        id: plan.id,
                        credit: plan.credit,
                      })
                    : floor.drawing_id
                  return (
                    <li
                      key={floor.drawing_id}
                      data-builder-floor={floor.drawing_id}
                      className="flex flex-wrap items-center gap-2 rounded-lg border bg-background px-3 py-2"
                    >
                      <span className="num flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-foreground">
                        {idx + 1}
                      </span>
                      <Input
                        value={floor.label}
                        maxLength={24}
                        onChange={(e) =>
                          updateFloorLabel(floor.drawing_id, e.target.value)
                        }
                        aria-label={`${tu("proj_floor_label")}: ${planTitle}`}
                        className="h-10 w-24 shrink-0 text-xs font-semibold"
                      />
                      <span
                        className="min-w-32 flex-1 truncate text-sm text-foreground"
                        title={planTitle}
                      >
                        {planTitle}
                      </span>
                      <div className="ml-auto flex shrink-0 items-center gap-1">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          disabled={idx === 0}
                          aria-label={extra.moveUp(floor.label || planTitle)}
                          onClick={() => moveFloor(idx, -1)}
                        >
                          <ArrowUpIcon />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          disabled={idx === draftFloors.length - 1}
                          aria-label={extra.moveDown(floor.label || planTitle)}
                          onClick={() => moveFloor(idx, 1)}
                        >
                          <ArrowDownIcon />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          aria-label={extra.removePlan(planTitle)}
                          onClick={() => removeFloorFromDraft(floor.drawing_id)}
                        >
                          <XIcon />
                        </Button>
                      </div>
                    </li>
                  )
                })}
              </ol>
            ) : (
              <p className="text-xs text-muted-foreground">
                {tu("proj_select_hint")}
              </p>
            )}

            <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
              <p className="text-xs text-muted-foreground">
                {draftFloors.length < 2
                  ? tu("proj_select_hint")
                  : tu("proj_selected_n", { n: draftFloors.length })}
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <Button type="button" variant="ghost" onClick={resetBuilder}>
                  {tu("proj_group_cancel")}
                </Button>
                <Button
                  type="button"
                  disabled={draftFloors.length < 2}
                  onClick={handleSave}
                  data-save-project
                >
                  <CheckIcon data-icon="inline-start" />
                  {tu("proj_save")}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {allProjects.length > 0 ? (
        <section
          aria-labelledby="projects-heading"
          className="flex min-w-0 flex-col gap-2.5"
        >
          <div className="flex flex-col gap-0.5">
            <h3
              id="projects-heading"
              className="flex items-center gap-2 text-sm font-medium text-foreground"
            >
              <Building2Icon
                className="size-4 shrink-0 text-muted-foreground"
                aria-hidden
              />
              <span>{tu("proj_section_title")}</span>
              <span className="num text-xs font-normal text-muted-foreground">
                {allProjects.length}
              </span>
            </h3>
            <p className="text-xs text-muted-foreground">
              {tu("proj_section_desc")}
            </p>
          </div>

          <ul
            className="grid grid-cols-1 gap-3 sm:grid-cols-2"
            aria-label={tu("proj_section_title")}
          >
            {allProjects.map((bundle) => {
              const sortedFloors = [...bundle.floors].sort(
                (a, b) =>
                  a.order - b.order || a.drawing_id.localeCompare(b.drawing_id)
              )
              const entryFloor = entryFloorOf(bundle)
              return (
                <li key={bundle.id} data-project-card={bundle.id} className="min-w-0">
                  <Card size="sm" className="h-full">
                    <CardContent className="flex h-full min-w-0 flex-col justify-between gap-3 pt-4">
                      <div className="flex min-w-0 flex-col gap-2">
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div className="min-w-0 flex-1">
                            <p
                              className="truncate text-sm font-semibold text-foreground"
                              title={bundle.title}
                            >
                              {bundle.title}
                            </p>
                            <p className="num text-xs text-muted-foreground">
                              {tu("ug_sheets_n", { n: sortedFloors.length })}
                            </p>
                          </div>
                          <span className="flex shrink-0 flex-wrap justify-end gap-1" data-project-types>
                            {[
                              ...new Set(
                                sortedFloors.map((f) => {
                                  const plan = planById.get(f.drawing_id)
                                  return fileTypeOf({ title: plan?.title ?? f.drawing_id, kind: plan?.kind ?? "raster" })
                                })
                              ),
                            ].map((t) => (
                              <Badge key={t} variant="outline" className="text-xs">
                                {t === "IMAGE" ? tu("kind_image") : t}
                              </Badge>
                            ))}
                          </span>
                        </div>

                        <div
                          className="flex flex-wrap gap-1.5"
                          role="group"
                          aria-label={bundle.title}
                        >
                          {sortedFloors.map((floor) => {
                            const plan = planById.get(floor.drawing_id)
                            const planName = plan
                              ? displayDrawingTitle({
                                  title: plan.title,
                                  id: plan.id,
                                  credit: plan.credit,
                                })
                              : floor.drawing_id
                            return (
                              <Button
                                key={floor.drawing_id}
                                variant="outline"
                                asChild
                                className="min-h-10 min-w-10 max-w-full px-3 text-xs font-medium"
                              >
                                <Link
                                  to={`/d/${encodeURIComponent(floor.drawing_id)}`}
                                  title={planName}
                                  onClick={() => activateProjectInWorkspace(bundle)}
                                >
                                  <span className="max-w-44 truncate">
                                    {floor.label}
                                  </span>
                                </Link>
                              </Button>
                            )
                          })}
                        </div>
                      </div>

                      <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-2.5">
                        <Button
                          variant="default"
                          asChild
                          className="min-h-10 text-xs"
                        >
                          <Link
                            to={`/d/${encodeURIComponent(entryFloor.drawing_id)}`}
                            onClick={() => activateProjectInWorkspace(bundle)}
                          >
                            <LayersIcon data-icon="inline-start" />
                            {tu("proj_open")}
                          </Link>
                        </Button>
                        <div className="flex flex-wrap items-center gap-1">
                          <Button
                            type="button"
                            variant="ghost"
                            aria-label={`${extra.editProject} ${bundle.title}`}
                            onClick={() => startEdit(bundle)}
                            className="min-h-10 text-xs"
                          >
                            <PencilIcon data-icon="inline-start" />
                            {extra.editProject}
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            aria-label={`${tu("proj_ungroup")} ${bundle.title}`}
                            onClick={() => handleUngroup(bundle)}
                            className="min-h-10 text-xs text-muted-foreground"
                          >
                            <UnlinkIcon data-icon="inline-start" />
                            {tu("proj_ungroup")}
                          </Button>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                </li>
              )
            })}
          </ul>
        </section>
      ) : null}
    </div>
  )
}

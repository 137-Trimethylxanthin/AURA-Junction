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
import type { Credit, DrawingSummary, Lang } from "@/api/contract"
import { displayDrawingTitle } from "@/api/library"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { useUi } from "@/i18n/uiLang"
import { actions } from "@/store"

export interface ProjectFloorItem {
  drawing_id: string
  label: string
  order: number
}

export interface ProjectBundleItem {
  id: string
  title: string
  floors: ProjectFloorItem[]
  auto_matched?: boolean
  created_at?: string
}

export interface SelectablePlan {
  id: string
  title: string
  subtitle?: string
  credit?: Credit
  source?: "library" | "upload"
  project_id?: string
  floor_label?: string
  floor_order?: number
}

interface Copy {
  sectionTitle: string
  sectionDesc: string
  autoBadge: string
  floorsN: (n: number) => string
  openProject: string
  editProject: string
  ungroup: string
  groupBtn: string
  cancelBtn: string
  titleLabel: string
  titlePlaceholder: string
  floorLabel: string
  moveUp: (label: string) => string
  moveDown: (label: string) => string
  removePlan: (title: string) => string
  selectHint: string
  selectedN: (n: number) => string
  saveBtn: string
  savedToast: (n: number, title: string) => string
  ungroupedToast: (title: string) => string
  uploadsPickLabel: string
}

const COPY: Record<Lang, Copy> = {
  en: {
    sectionTitle: "House & building projects",
    sectionDesc:
      "Plans grouped by building so you can switch floors fast and match stairs, shafts or apartments across sheets.",
    autoBadge: "Auto-matched",
    floorsN: (n) => `${n} floors`,
    openProject: "Open project",
    editProject: "Edit",
    ungroup: "Ungroup",
    groupBtn: "Group plans into a project",
    cancelBtn: "Cancel grouping",
    titleLabel: "Project name",
    titlePlaceholder: "e.g. Villa Nord",
    floorLabel: "Floor label",
    moveUp: (l) => `Move ${l} up`,
    moveDown: (l) => `Move ${l} down`,
    removePlan: (t) => `Remove ${t} from project`,
    selectHint: "Select 2 or more plans below to bundle them into a multi-floor project.",
    selectedN: (n) => `${n} plans selected`,
    saveBtn: "Save project",
    savedToast: (n, title) => `Grouped ${n} floors into ${title}`,
    ungroupedToast: (title) => `Ungrouped ${title}`,
    uploadsPickLabel: "Your ready uploads",
  },
  fr: {
    sectionTitle: "Projets de bâtiment",
    sectionDesc:
      "Plans regroupés par bâtiment pour changer d’étage rapidement et relier escaliers, gaines ou appartements.",
    autoBadge: "Associé auto",
    floorsN: (n) => `${n} étages`,
    openProject: "Ouvrir le projet",
    editProject: "Modifier",
    ungroup: "Dissocier",
    groupBtn: "Grouper des plans en projet",
    cancelBtn: "Annuler le groupement",
    titleLabel: "Nom du projet",
    titlePlaceholder: "p. ex. Villa Nord",
    floorLabel: "Nom de l’étage",
    moveUp: (l) => `Monter ${l}`,
    moveDown: (l) => `Descendre ${l}`,
    removePlan: (t) => `Retirer ${t} du projet`,
    selectHint: "Sélectionnez au moins 2 plans ci-dessous pour les regrouper en projet multi-étages.",
    selectedN: (n) => `${n} plans sélectionnés`,
    saveBtn: "Enregistrer le projet",
    savedToast: (n, title) => `${n} étages regroupés dans ${title}`,
    ungroupedToast: (title) => `${title} dissocié`,
    uploadsPickLabel: "Vos fichiers prêts",
  },
  fi: {
    sectionTitle: "Rakennusprojektit",
    sectionDesc:
      "Rakennuksen pohjat ryhmiteltynä nopeaan kerrosvaihtoon sekä portaiden, kuilujen ja asuntojen vertailuun.",
    autoBadge: "Automaattinen",
    floorsN: (n) => `${n} kerrosta`,
    openProject: "Avaa projekti",
    editProject: "Muokkaa",
    ungroup: "Pura ryhmä",
    groupBtn: "Ryhmittele pohjat projektiksi",
    cancelBtn: "Peruuta ryhmittely",
    titleLabel: "Projektin nimi",
    titlePlaceholder: "esim. Villa Nord",
    floorLabel: "Kerrostunnus",
    moveUp: (l) => `Siirrä ${l} ylemmäs`,
    moveDown: (l) => `Siirrä ${l} alemmas`,
    removePlan: (t) => `Poista ${t} projektista`,
    selectHint: "Valitse alta vähintään 2 piirustusta yhdistääksesi ne monikerrosprojektiksi.",
    selectedN: (n) => `${n} piirustusta valittu`,
    saveBtn: "Tallenna projekti",
    savedToast: (n, title) => `${n} kerrosta ryhmitelty projektiin ${title}`,
    ungroupedToast: (title) => `${title} purettu`,
    uploadsPickLabel: "Valmiit omat lataukset",
  },
  de: {
    sectionTitle: "Haus- & Gebäudeprojekte",
    sectionDesc:
      "Nach Gebäude gruppierte Pläne für schnellen Geschosswechsel und geschossübergreifenden Abgleich von Treppen, Schächten oder Wohnungen.",
    autoBadge: "Automatisch erkannt",
    floorsN: (n) => `${n} Geschosse`,
    openProject: "Projekt öffnen",
    editProject: "Bearbeiten",
    ungroup: "Gruppierung lösen",
    groupBtn: "Pläne zu Projekt gruppieren",
    cancelBtn: "Gruppierung abbrechen",
    titleLabel: "Projektname",
    titlePlaceholder: "z. B. Villa Nord",
    floorLabel: "Geschosskürzel",
    moveUp: (l) => `${l} nach oben`,
    moveDown: (l) => `${l} nach unten`,
    removePlan: (t) => `${t} aus Projekt entfernen`,
    selectHint: "Wählen Sie unten mindestens 2 Pläne aus, um sie als Mehrgeschoss-Projekt zu bündeln.",
    selectedN: (n) => `${n} Pläne ausgewählt`,
    saveBtn: "Projekt speichern",
    savedToast: (n, title) => `${n} Geschosse in ${title} gruppiert`,
    ungroupedToast: (title) => `Gruppierung für ${title} gelöst`,
    uploadsPickLabel: "Bereite eigene Uploads",
  },
}

const PROJECTS_KEY = "aura.projects"
const DISMISSED_AUTO_KEY = "aura.projects.dismissed"
const PROJECTS_EVENT = "aura:projects"

function readStorage(key: string): string | null {
  try {
    return globalThis.localStorage?.getItem(key) ?? null
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string | null) {
  try {
    if (value === null) globalThis.localStorage?.removeItem(key)
    else globalThis.localStorage?.setItem(key, value)
  } catch {
    /* storage unavailable in private mode */
  }
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(PROJECTS_EVENT))
  }
}

function subscribeProjectsStore(onStoreChange: () => void) {
  if (typeof window === "undefined") return () => {}
  window.addEventListener(PROJECTS_EVENT, onStoreChange)
  window.addEventListener("storage", onStoreChange)
  return () => {
    window.removeEventListener(PROJECTS_EVENT, onStoreChange)
    window.removeEventListener("storage", onStoreChange)
  }
}

function isFloorItem(x: unknown): x is ProjectFloorItem {
  return (
    !!x &&
    typeof x === "object" &&
    typeof (x as ProjectFloorItem).drawing_id === "string" &&
    typeof (x as ProjectFloorItem).label === "string" &&
    typeof (x as ProjectFloorItem).order === "number"
  )
}

function isBundleItem(x: unknown): x is ProjectBundleItem {
  return (
    !!x &&
    typeof x === "object" &&
    typeof (x as ProjectBundleItem).id === "string" &&
    typeof (x as ProjectBundleItem).title === "string" &&
    Array.isArray((x as ProjectBundleItem).floors) &&
    (x as ProjectBundleItem).floors.every(isFloorItem)
  )
}

function parseStoredProjects(raw: string | null): ProjectBundleItem[] {
  try {
    const parsed: unknown = JSON.parse(raw ?? "[]")
    return Array.isArray(parsed) ? parsed.filter(isBundleItem) : []
  } catch {
    return []
  }
}

function writeStoredProjects(projects: ProjectBundleItem[]) {
  writeStorage(PROJECTS_KEY, JSON.stringify(projects))
}

function parseDismissedIds(raw: string | null): Set<string> {
  try {
    const parsed: unknown = JSON.parse(raw ?? "[]")
    return new Set(Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [])
  } catch {
    return new Set()
  }
}

function addDismissedAutoId(id: string) {
  const next = parseDismissedIds(readStorage(DISMISSED_AUTO_KEY))
  next.add(id)
  writeStorage(DISMISSED_AUTO_KEY, JSON.stringify([...next]))
}

function removeDismissedAutoId(id: string) {
  const next = parseDismissedIds(readStorage(DISMISSED_AUTO_KEY))
  if (next.delete(id)) {
    writeStorage(DISMISSED_AUTO_KEY, JSON.stringify([...next]))
  }
}

interface InferredFloor {
  order: number
  label: string
  matched: boolean
}

function inferFloor(plan: SelectablePlan, fallbackIndex = 0): InferredFloor {
  if (typeof plan.floor_order === "number" && plan.floor_label?.trim()) {
    return { order: plan.floor_order, label: plan.floor_label.trim(), matched: true }
  }
  if (plan.floor_label?.trim()) {
    return { order: plan.floor_order ?? fallbackIndex, label: plan.floor_label.trim(), matched: true }
  }

  const hay = `${plan.title} ${plan.subtitle ?? ""} ${plan.id}`

  // Multi-basement: 2. UG, B2, 2e sous-sol
  const multiUg = /\b([1-9])\.?\s*UG\b|\bB([1-9])\b|\b([1-9])(?:er|e|ème)?\s*sous[-\s]?sol\b/i.exec(hay)
  if (multiUg) {
    const n = Number(multiUg[1] || multiUg[2] || multiUg[3] || 1)
    const label = /\bB[1-9]\b/i.test(multiUg[0]) ? `B${n}` : n === 1 ? "UG" : `${n}. UG`
    return { order: -n, label, matched: true }
  }

  // Basement / cellar
  if (/\b(KG|UG|kellergeschoss|untergeschoss|keller|basement|cellar|sous[-\s]?sol|kellari|kellarikerros)\b/i.test(hay)) {
    const isUg = /\b(UG|untergeschoss)\b/i.test(hay)
    const isFr = /\bsous[-\s]?sol\b/i.test(hay)
    const isEn = /\b(basement|cellar)\b/i.test(hay)
    return { order: -1, label: isUg ? "UG" : isFr ? "SS" : isEn ? "B1" : "KG", matched: true }
  }

  // Ground floor
  if (
    /\b(EG|erdgeschoss|parterre|ground\s*floor|GF|rez[-\s]?de[-\s]?chauss[ée]e|RDC|pohjakerros|maantasokerros|level\s*0|floor\s*0)\b/i.test(
      hay
    )
  ) {
    const isFr = /\b(rez|RDC)\b/i.test(hay)
    const isEn = /\b(ground\s*floor|GF|level\s*0|floor\s*0)\b/i.test(hay)
    return { order: 0, label: isFr ? "RDC" : isEn ? "GF" : "EG", matched: true }
  }

  // German upper floors: 1. OG, 2 OG, 1OG, Obergeschoss
  const deOg = /(?:^|[\s._-])([1-9]\d?)\.?\s*OG\b/i.exec(hay)
  if (deOg) {
    const n = Number(deOg[1])
    return { order: n, label: `${n}. OG`, matched: true }
  }
  if (/\bobergeschoss\b/i.test(hay)) {
    return { order: 1, label: "1. OG", matched: true }
  }

  // French upper floors: 1er étage, 3e étage
  const frEt = /\b([1-9]\d?)\s*(?:er|e|ème)\s*[ée]tage\b/i.exec(hay)
  if (frEt) {
    const n = Number(frEt[1])
    return { order: n, label: `${n === 1 ? "1er" : `${n}e`}`, matched: true }
  }

  // Finnish floors: 1. kerros, 2. krs
  const fiKrs = /\b([1-9]\d?)\.\s*(?:kerros|krs)\b/i.exec(hay)
  if (fiKrs) {
    const n = Number(fiKrs[1])
    return { order: n - 1, label: `${n}. krs`, matched: true }
  }

  // English floors: 1st floor, 2nd floor, Floor 2, Level 3, Storey 1
  const enOrd = /\b([1-9]\d?)(?:st|nd|rd|th)\s*(?:floor|storey|level)\b/i.exec(hay)
  if (enOrd) {
    const n = Number(enOrd[1])
    return { order: n, label: `${n}F`, matched: true }
  }
  const enPre = /\b(?:floor|storey|level)\s*([1-9]\d?)\b/i.exec(hay)
  if (enPre) {
    const n = Number(enPre[1])
    return { order: n, label: `${n}F`, matched: true }
  }

  // Attic / Roof
  if (/\b(DG|dachgeschoss|attika|attic|penthouse|roof\s*floor|combles|toiture|ullakko)\b/i.test(hay)) {
    return { order: 90, label: "DG", matched: true }
  }

  // Multi-page PDFs: Page 1, Seite 2, sivu 3, --p2, _page_2
  const pageMatch = /\b(?:page|seite|sivu)\s*([1-9]\d?)\b|[._-]p(?:age)?([1-9]\d?)\b/i.exec(hay)
  if (pageMatch) {
    const n = Number(pageMatch[1] || pageMatch[2])
    return { order: n, label: `P${n}`, matched: true }
  }

  return { order: 100 + fallbackIndex, label: `F${fallbackIndex + 1}`, matched: false }
}

const FLOOR_STRIP_RE =
  /\b([1-9]\.?\s*(?:UG|OG|kerros|krs)|[1-9](?:st|nd|rd|th)\s*(?:floor|storey|level)|(?:floor|storey|level|page|seite|sivu)\s*\d+|[1-9]\s*(?:er|e|ème)\s*[ée]tage|KG|UG|EG|DG|GF|RDC|B[1-9]|kellergeschoss|untergeschoss|erdgeschoss|obergeschoss|dachgeschoss|attika|ground\s*floor|first\s*floor|second\s*floor|third\s*floor|basement|cellar|attic|rez[-\s]?de[-\s]?chauss[ée]e|sous[-\s]?sol|combles|pohjakerros|kellari|ullakko)\b/gi

function cleanStem(raw: string): string {
  return raw
    .replace(/\.(dxf|ifc|pdf|png|jpe?g|webp)$/i, "")
    .replace(FLOOR_STRIP_RE, " ")
    .replace(/[·—–\-_:()/[\]]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
}

function buildingStemOf(plan: SelectablePlan): { key: string; title: string } | null {
  const display = displayDrawingTitle({
    title: plan.title,
    id: plan.id,
    credit: plan.credit,
  })
  const stem = cleanStem(display)
  const lower = stem.toLowerCase()
  if (stem.length >= 3 && !/^(plan|drawing|floor\s*plan|grundriss|upload|scan|image|model)$/i.test(lower)) {
    return { key: lower, title: stem }
  }
  const siteMatch = /\bsite\s+(\d+)\b/i.exec(plan.subtitle ?? "")
  if (siteMatch) {
    return { key: `site-${siteMatch[1]}`, title: `Site ${siteMatch[1]}` }
  }
  return null
}

function autoMatchPlans(
  plans: SelectablePlan[],
  saved: ProjectBundleItem[],
  dismissedIds: Set<string>
): ProjectBundleItem[] {
  const claimedDrawings = new Set<string>()
  const savedIds = new Set<string>()
  for (const p of saved) {
    savedIds.add(p.id)
    for (const f of p.floors) claimedDrawings.add(f.drawing_id)
  }

  const out: ProjectBundleItem[] = []

  // 1. Explicit server/PDF `project_id` grouping
  const byProjectId = new Map<string, SelectablePlan[]>()
  for (const plan of plans) {
    if (!plan.project_id || claimedDrawings.has(plan.id)) continue
    const arr = byProjectId.get(plan.project_id) ?? []
    arr.push(plan)
    byProjectId.set(plan.project_id, arr)
  }
  for (const [projId, group] of byProjectId) {
    if (group.length < 2 || savedIds.has(projId) || dismissedIds.has(projId)) continue
    const floors = group
      .map((p, idx) => {
        const inf = inferFloor(p, idx)
        return { drawing_id: p.id, label: inf.label, order: inf.order }
      })
      .sort((a, b) => a.order - b.order)
    const stem = buildingStemOf(group[0])
    out.push({
      id: projId,
      title: stem?.title || displayDrawingTitle({ title: group[0].title, id: group[0].id, credit: group[0].credit }),
      floors,
      auto_matched: true,
    })
    for (const p of group) claimedDrawings.add(p.id)
  }

  // 2. Building stem + distinct floor indicator matching
  const byStem = new Map<string, { title: string; items: { plan: SelectablePlan; floor: InferredFloor }[] }>()
  for (const plan of plans) {
    if (claimedDrawings.has(plan.id)) continue
    const floor = inferFloor(plan)
    if (!floor.matched) continue
    const stem = buildingStemOf(plan)
    if (!stem) continue
    const entry = byStem.get(stem.key) ?? { title: stem.title, items: [] }
    entry.items.push({ plan, floor })
    byStem.set(stem.key, entry)
  }

  for (const [stemKey, { title, items }] of byStem) {
    if (items.length < 2) continue
    const distinctLabels = new Set(items.map((x) => x.floor.label.toLowerCase()))
    if (distinctLabels.size < 2) continue
    const autoId = `auto-${stemKey.replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}`
    if (savedIds.has(autoId) || dismissedIds.has(autoId)) continue
    const floors = items
      .map((x) => ({
        drawing_id: x.plan.id,
        label: x.floor.label,
        order: x.floor.order,
      }))
      .sort((a, b) => a.order - b.order)
    out.push({
      id: autoId,
      title,
      floors,
      auto_matched: true,
    })
  }

  return out
}

function syncProjectToServer(base: string | null, bundle: ProjectBundleItem) {
  if (!base) return
  void fetch(`${base.replace(/\/+$/, "")}/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(bundle),
  }).catch(() => {
    /* localStorage remains authoritative when offline or before /projects is enabled */
  })
}

function deleteProjectOnServer(base: string | null, projectId: string) {
  if (!base) return
  void fetch(`${base.replace(/\/+$/, "")}/projects/${encodeURIComponent(projectId)}`, {
    method: "DELETE",
  }).catch(() => {
    /* ignore offline / 404 */
  })
}

function activateProjectInWorkspace(bundle: ProjectBundleItem) {
  // Ensure `aura.projects` in localStorage has this bundle so FloorSwitcher on `/d/:id` finds it immediately.
  const current = parseStoredProjects(readStorage(PROJECTS_KEY))
  if (!current.some((p) => p.id === bundle.id)) {
    writeStoredProjects([bundle, ...current])
  }
  const storeActions = actions() as unknown as {
    setActiveProject?: (project: ProjectBundleItem | null) => void
  }
  storeActions.setActiveProject?.(bundle)
}

function entryFloorOf(bundle: ProjectBundleItem): ProjectFloorItem {
  const sorted = [...bundle.floors].sort((a, b) => a.order - b.order)
  return sorted.find((f) => f.order === 0) ?? sorted[0]
}

export interface ProjectBundlerBarProps {
  base: string | null
  plans: SelectablePlan[]
  librarySummaries?: DrawingSummary[]
  grouping: boolean
  onGroupingChange: (open: boolean) => void
  selectedIds: string[]
  onSelectedIdsChange: (ids: string[]) => void
  onSelectionLabelsChange?: (labels: Record<string, string>) => void
}

export function ProjectBundlerBar({
  base,
  plans,
  grouping,
  onGroupingChange,
  selectedIds,
  onSelectedIdsChange,
  onSelectionLabelsChange,
}: ProjectBundlerBarProps) {
  const { lang } = useUi()
  const copy = COPY[lang]

  const rawSaved = useSyncExternalStore(
    subscribeProjectsStore,
    () => readStorage(PROJECTS_KEY),
    () => null
  )
  const rawDismissed = useSyncExternalStore(
    subscribeProjectsStore,
    () => readStorage(DISMISSED_AUTO_KEY),
    () => null
  )

  const savedProjects = useMemo(() => parseStoredProjects(rawSaved), [rawSaved])
  const dismissedAuto = useMemo(() => parseDismissedIds(rawDismissed), [rawDismissed])

  const [editingId, setEditingId] = useState<string | null>(null)
  const [customTitle, setCustomTitle] = useState<string | null>(null)
  const [labelOverrides, setLabelOverrides] = useState<Record<string, string>>({})
  const [manuallyReordered, setManuallyReordered] = useState(false)

  const planById = useMemo(() => new Map(plans.map((p) => [p.id, p])), [plans])

  const autoProjects = useMemo(
    () => autoMatchPlans(plans, savedProjects, dismissedAuto),
    [plans, savedProjects, dismissedAuto]
  )

  const allProjects = useMemo(() => {
    const merged = [...savedProjects, ...autoProjects]
    return merged.filter((p) => p.floors.length >= 2)
  }, [savedProjects, autoProjects])

  const draftFloors = useMemo<ProjectFloorItem[]>(() => {
    if (!grouping) return []
    const items = selectedIds.map((id, idx) => {
      const plan = planById.get(id)
      const inf = plan
        ? inferFloor(plan, idx)
        : { order: 100 + idx, label: `F${idx + 1}`, matched: false }
      const label = labelOverrides[id] !== undefined ? labelOverrides[id] : inf.label
      return {
        drawing_id: id,
        label,
        order: manuallyReordered ? idx : inf.order,
      }
    })
    return manuallyReordered ? items : [...items].sort((a, b) => a.order - b.order)
  }, [grouping, selectedIds, planById, labelOverrides, manuallyReordered])

  const suggestedTitle = useMemo(() => {
    if (!grouping) return ""
    const selectedPlans = selectedIds.map((id) => planById.get(id)).filter((p): p is SelectablePlan => !!p)
    if (!selectedPlans.length) return ""
    const stems = selectedPlans.map(buildingStemOf).filter((s): s is { key: string; title: string } => !!s)
    if (stems.length > 0 && stems.every((s) => s.key === stems[0].key)) {
      return stems[0].title
    }
    if (selectedPlans.length >= 2 && stems[0]?.title) {
      return stems[0].title
    }
    return ""
  }, [grouping, selectedIds, planById])

  const displayedTitle = customTitle ?? suggestedTitle

  // Sync derived floor badges back to `LibraryPage` so `DrawingCard` displays `KG`, `EG`, `1. OG`.
  useEffect(() => {
    if (!onSelectionLabelsChange) return
    if (!grouping) {
      onSelectionLabelsChange({})
      return
    }
    const map: Record<string, string> = {}
    draftFloors.forEach((f, idx) => {
      map[f.drawing_id] = f.label.trim() || `F${idx + 1}`
    })
    onSelectionLabelsChange(map)
  }, [grouping, draftFloors, onSelectionLabelsChange])

  const resetBuilder = useCallback(() => {
    setEditingId(null)
    setCustomTitle(null)
    setLabelOverrides({})
    setManuallyReordered(false)
    onSelectedIdsChange([])
    onGroupingChange(false)
  }, [onGroupingChange, onSelectedIdsChange])

  const startEdit = useCallback(
    (bundle: ProjectBundleItem) => {
      const sorted = [...bundle.floors].sort((a, b) => a.order - b.order)
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
    (bundle: ProjectBundleItem) => {
      const nextSaved = savedProjects.filter((p) => p.id !== bundle.id)
      writeStoredProjects(nextSaved)
      if (bundle.auto_matched || bundle.id.startsWith("auto-")) {
        addDismissedAutoId(bundle.id)
      }
      deleteProjectOnServer(base, bundle.id)
      const storeActions = actions() as unknown as {
        setActiveProject?: (project: ProjectBundleItem | null) => void
      }
      storeActions.setActiveProject?.(null)
      if (editingId === bundle.id) resetBuilder()
      toast.success(copy.ungroupedToast(bundle.title))
    },
    [savedProjects, base, editingId, resetBuilder, copy]
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
      buildingStemOf(planById.get(draftFloors[0].drawing_id) ?? { id: "", title: "" })?.title ||
      copy.sectionTitle
    const normalizedFloors: ProjectFloorItem[] = draftFloors.map((f, idx) => ({
      drawing_id: f.drawing_id,
      label: f.label.trim() || `F${idx + 1}`,
      order: idx,
    }))
    const id = editingId && !editingId.startsWith("auto-") ? editingId : `proj-${Date.now().toString(36)}`
    if (editingId?.startsWith("auto-")) {
      addDismissedAutoId(editingId)
    } else {
      removeDismissedAutoId(id)
    }
    const bundle: ProjectBundleItem = {
      id,
      title: cleanTitle,
      floors: normalizedFloors,
      auto_matched: false,
      created_at: new Date().toISOString(),
    }
    const nextSaved = [bundle, ...savedProjects.filter((p) => p.id !== id && p.id !== editingId)]
    writeStoredProjects(nextSaved)
    syncProjectToServer(base, bundle)
    toast.success(copy.savedToast(normalizedFloors.length, cleanTitle))
    resetBuilder()
  }

  const uploadPlans = useMemo(
    () => plans.filter((p) => p.source === "upload"),
    [plans]
  )

  if (!grouping && allProjects.length === 0) return null

  return (
    <div className="flex flex-col gap-3" data-projects-region>
      {grouping ? (
        <Card size="sm" className="border-info/40 bg-info-surface/25" data-project-builder>
          <CardHeader className="pb-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                <LayersIcon className="size-4 text-info" aria-hidden />
                <span>{copy.groupBtn}</span>
              </CardTitle>
              <Badge variant="secondary" className="num text-xs">
                {copy.selectedN(draftFloors.length)}
              </Badge>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5 sm:max-w-sm">
              <label htmlFor="project-title-input" className="text-xs font-medium text-foreground">
                {copy.titleLabel}
              </label>
              <Input
                id="project-title-input"
                value={displayedTitle}
                onChange={(e) => setCustomTitle(e.target.value)}
                placeholder={copy.titlePlaceholder}
                className="h-10 bg-background"
              />
            </div>

            {uploadPlans.length > 0 ? (
              <div className="flex flex-col gap-1.5">
                <span className="text-xs font-medium text-muted-foreground">
                  {copy.uploadsPickLabel}
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
                        className="min-h-10 min-w-10 text-xs"
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
                    ? displayDrawingTitle({ title: plan.title, id: plan.id, credit: plan.credit })
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
                        onChange={(e) => updateFloorLabel(floor.drawing_id, e.target.value)}
                        aria-label={`${copy.floorLabel}: ${planTitle}`}
                        className="h-10 w-24 shrink-0 text-xs font-semibold"
                      />
                      <span className="min-w-0 flex-1 truncate text-sm text-foreground" title={planTitle}>
                        {planTitle}
                      </span>
                      <div className="flex items-center gap-1">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          disabled={idx === 0}
                          aria-label={copy.moveUp(floor.label || planTitle)}
                          onClick={() => moveFloor(idx, -1)}
                        >
                          <ArrowUpIcon />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          disabled={idx === draftFloors.length - 1}
                          aria-label={copy.moveDown(floor.label || planTitle)}
                          onClick={() => moveFloor(idx, 1)}
                        >
                          <ArrowDownIcon />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          aria-label={copy.removePlan(planTitle)}
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
              <p className="text-xs text-muted-foreground">{copy.selectHint}</p>
            )}

            <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
              <p className="text-xs text-muted-foreground">
                {draftFloors.length < 2 ? copy.selectHint : copy.selectedN(draftFloors.length)}
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <Button type="button" variant="ghost" onClick={resetBuilder}>
                  {copy.cancelBtn}
                </Button>
                <Button
                  type="button"
                  disabled={draftFloors.length < 2}
                  onClick={handleSave}
                  data-save-project
                >
                  <CheckIcon data-icon="inline-start" />
                  {copy.saveBtn}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {allProjects.length > 0 ? (
        <section aria-labelledby="projects-heading" className="flex flex-col gap-2.5">
          <div className="flex flex-col gap-0.5">
            <h3 id="projects-heading" className="flex items-center gap-2 text-sm font-medium text-foreground">
              <Building2Icon className="size-4 text-muted-foreground" aria-hidden />
              <span>{copy.sectionTitle}</span>
              <span className="num text-xs font-normal text-muted-foreground">{allProjects.length}</span>
            </h3>
            <p className="text-xs text-muted-foreground">{copy.sectionDesc}</p>
          </div>

          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2" aria-label={copy.sectionTitle}>
            {allProjects.map((bundle) => {
              const sortedFloors = [...bundle.floors].sort((a, b) => a.order - b.order)
              const entryFloor = entryFloorOf(bundle)
              return (
                <li key={bundle.id} data-project-card={bundle.id}>
                  <Card size="sm" className="h-full">
                    <CardContent className="flex h-full flex-col justify-between gap-3 pt-4">
                      <div className="flex flex-col gap-2">
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-semibold text-foreground" title={bundle.title}>
                              {bundle.title}
                            </p>
                            <p className="num text-xs text-muted-foreground">
                              {copy.floorsN(sortedFloors.length)}
                            </p>
                          </div>
                          {bundle.auto_matched ? (
                            <Badge variant="secondary" className="shrink-0 text-xs">
                              {copy.autoBadge}
                            </Badge>
                          ) : null}
                        </div>

                        <div className="flex flex-wrap gap-1.5" role="group" aria-label={bundle.title}>
                          {sortedFloors.map((floor) => {
                            const plan = planById.get(floor.drawing_id)
                            const planName = plan
                              ? displayDrawingTitle({ title: plan.title, id: plan.id, credit: plan.credit })
                              : floor.drawing_id
                            return (
                              <Button
                                key={floor.drawing_id}
                                variant="outline"
                                asChild
                                className="min-h-10 min-w-10 px-3 text-xs font-medium"
                              >
                                <Link
                                  to={`/d/${encodeURIComponent(floor.drawing_id)}`}
                                  title={planName}
                                  onClick={() => activateProjectInWorkspace(bundle)}
                                >
                                  {floor.label}
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
                            {copy.openProject}
                          </Link>
                        </Button>
                        <div className="flex items-center gap-1">
                          <Button
                            type="button"
                            variant="ghost"
                            aria-label={`${copy.editProject} ${bundle.title}`}
                            onClick={() => startEdit(bundle)}
                            className="min-h-10 text-xs"
                          >
                            <PencilIcon data-icon="inline-start" />
                            {copy.editProject}
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            aria-label={`${copy.ungroup} ${bundle.title}`}
                            onClick={() => handleUngroup(bundle)}
                            className="min-h-10 text-xs text-muted-foreground"
                          >
                            <UnlinkIcon data-icon="inline-start" />
                            {copy.ungroup}
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

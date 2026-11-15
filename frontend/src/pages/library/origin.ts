import type { UiKey } from "@/i18n/strings"
import type { DrawingSummary } from "@/api/contract"

/** The collection a library entry comes from: the source filter beside the file-type chips. */
export type Origin = "swiss" | "ifc" | "cubicasa" | "us_sheets" | "historic" | "uploads" | "other"

export const ORIGIN_ORDER: Origin[] = ["swiss", "ifc", "cubicasa", "us_sheets", "historic", "uploads", "other"]

export const ORIGIN_LABEL: Record<Origin, string> = {
  swiss: "Swiss Dwellings",
  ifc: "IFC models",
  cubicasa: "CubiCasa",
  us_sheets: "US sheets",
  historic: "Historic scans",
  uploads: "Uploads",
  other: "Other",
}

const ORIGIN_KEY: Partial<Record<Origin, UiKey>> = {
  ifc: "origin_ifc",
  us_sheets: "origin_us_sheets",
  historic: "origin_historic",
  uploads: "origin_uploads",
  other: "origin_other",
}

/** The chip of a source in the interface language; the names of data sets (Swiss Dwellings, CubiCasa) stay. */
export function originLabel(o: Origin, tu: (k: UiKey) => string): string {
  const key = ORIGIN_KEY[o]
  return key ? tu(key) : ORIGIN_LABEL[o]
}

/** The server says it for store drawings; bundles and uploads are told from what the summary already carries. */
export function originOf(d: DrawingSummary): Origin {
  if (d.source === "upload") return "uploads"
  if (d.origin && (ORIGIN_ORDER as string[]).includes(d.origin)) return d.origin
  if (d.kind === "ifc") return "ifc"
  const credit = (d.credit?.text ?? "").toLowerCase()
  if (credit.includes("swiss")) return "swiss"
  if (credit.includes("cubicasa")) return "cubicasa"
  if (credit.includes("openingdesign")) return "us_sheets"
  return "other"
}

/** The sources present, in a fixed order, with their counts (one pass, no request per card). */
export function originCounts(drawings: DrawingSummary[]): { o: Origin; n: number }[] {
  const n = new Map<Origin, number>()
  for (const d of drawings) n.set(originOf(d), (n.get(originOf(d)) ?? 0) + 1)
  return ORIGIN_ORDER.filter((o) => n.has(o)).map((o) => ({ o, n: n.get(o)! }))
}

/** A sheet the store read no room on (a detail sheet, an elevation, a letter): listed, but after the plans. */
export function isSheetWithoutPlan(d: DrawingSummary): boolean {
  return d.n_rooms === 0 && !d.n_doors
}

/**
 * Demo order without hiding anything: the given order is kept (own uploads, then the library as served), except
 * that plans with rooms come first, then sheets the store read only doors on (a site plan, a detail sheet with
 * one door symbol), then sheets without a plan. Stable, so the order inside each part is unchanged.
 */
export function plansFirst<T extends DrawingSummary>(drawings: T[]): T[] {
  const tier = (d: T) => (isSheetWithoutPlan(d) ? 2 : d.n_rooms === 0 ? 1 : 0)
  return [0, 1, 2].flatMap((t) => drawings.filter((d) => tier(d) === t))
}

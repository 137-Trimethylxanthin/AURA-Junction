import exported from "./facts.json"
import manual from "./facts.manual.json"

/**
 * Measured facts for the landing page. Every number on the page comes from facts.json.
 * An entry without value, n, scope, method or a measured date is dropped, never filled in.
 */
export interface Fact {
  id: string
  /** "result" renders in the measured section, "limit" in the limits section. */
  kind: "result" | "limit"
  label: string
  value: string
  n: number
  n_unit: string
  scope: string
  method: string
  measured: string
  numerator?: number
  denominator?: number
  model_calls?: number
  cost_usd?: number
  caveat?: string
}

const str = (v: unknown): v is string =>
  typeof v === "string" && v.trim().length > 0
const int = (v: unknown): v is number =>
  typeof v === "number" && Number.isInteger(v) && v >= 0
const num = (v: unknown): v is number =>
  typeof v === "number" && Number.isFinite(v) && v >= 0
const date = (v: unknown): v is string =>
  str(v) && /^\d{4}-\d{2}-\d{2}$/.test(v)

export function parseFact(v: unknown): Fact | null {
  if (!v || typeof v !== "object") return null
  const o = v as Record<string, unknown>
  if (
    !str(o.id) ||
    !str(o.label) ||
    !str(o.value) ||
    !str(o.scope) ||
    !str(o.method)
  )
    return null
  if (!int(o.n) || o.n < 1 || !str(o.n_unit) || !date(o.measured)) return null
  const fact: Fact = {
    id: o.id,
    kind:
      o.kind === "limit" ||
      (o.kind === undefined && o.id.startsWith("cubicasa-"))
        ? "limit"
        : "result",
    label: o.label,
    value: o.value,
    n: o.n,
    n_unit: o.n_unit,
    scope: o.scope,
    method: o.method,
    measured: o.measured,
  }
  // A fraction renders only when both halves are there and it is a real fraction.
  if (
    int(o.numerator) &&
    int(o.denominator) &&
    o.denominator > 0 &&
    o.numerator <= o.denominator
  ) {
    fact.numerator = o.numerator
    fact.denominator = o.denominator
  }
  if (int(o.model_calls)) fact.model_calls = o.model_calls
  if (num(o.cost_usd)) fact.cost_usd = o.cost_usd
  if (str(o.caveat)) fact.caveat = o.caveat
  return fact
}

export function parseFacts(data: unknown): Fact[] {
  const list = (data as { facts?: unknown } | null)?.facts
  if (!Array.isArray(list)) return []
  return list.map(parseFact).filter((f): f is Fact => f !== null)
}

/** facts.json is written by backend/eval/export_facts.py; facts.manual.json holds the measured facts it does not export. */
export const FACTS: Fact[] = [...parseFacts(exported), ...parseFacts(manual)]
export const RESULTS = FACTS.filter((f) => f.kind === "result")
export const LIMIT_FACTS = FACTS.filter((f) => f.kind === "limit")
export const factById = (id: string) => FACTS.find((f) => f.id === id)

const nf = new Intl.NumberFormat("en-GB")
export const fmtInt = (n: number) => nf.format(n)

/** "14 Nov 2026" */
export const fmtDate = (iso: string) =>
  new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${iso}T00:00:00Z`))

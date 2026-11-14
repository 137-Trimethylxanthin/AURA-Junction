import { useEffect, useState } from "react"
import { getBudget } from "@/api/client"
import type { Budget } from "@/api/contract"
import { apiBase } from "@/api/library"
import { useAppStore } from "@/store"

/**
 * The server's running model totals, read from `GET /budget`. Shows nothing until the server has answered, in local
 * mode, or when the endpoint is missing: never a guessed figure. Refreshed after every answer, since answers add to it.
 */
export function useBudget(): Budget | null {
  const api = useAppStore((s) => s.session.source.kind === "api")
  const answers = useAppStore((s) => s.turns.filter((t) => t.role === "agent" && t.response).length)
  const [budget, setBudget] = useState<Budget | null>(null)
  const baseUrl = apiBase()
  useEffect(() => {
    if (!api || !baseUrl) return
    const ctl = new AbortController()
    getBudget({ baseUrl, fetch: (u, init) => fetch(u, { ...init, signal: ctl.signal }) })
      .then((b) => {
        if (!ctl.signal.aborted) setBudget(b)
      })
      .catch(() => {
        if (!ctl.signal.aborted) setBudget(null)
      })
    return () => ctl.abort()
  }, [api, baseUrl, answers])
  return api && baseUrl ? budget : null
}

/** Total model calls and spend so far, or null when the server does not report them. Caps are never shown. */
export function budgetTotals(b: Budget | null): { calls: number; cost: number } | null {
  if (!b || typeof b.model_calls_total !== "number" || typeof b.cost_usd_total !== "number") return null
  return { calls: b.model_calls_total, cost: b.cost_usd_total }
}

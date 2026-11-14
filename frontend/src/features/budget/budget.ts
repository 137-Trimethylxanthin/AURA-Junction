import { useEffect, useState } from "react"
import { getBudget } from "@/api/client"
import type { Budget } from "@/api/contract"
import { apiBase } from "@/api/library"
import { useAppStore } from "@/store"

/**
 * The server's model-request budget, read from `GET /budget`. Shows nothing until the server has answered, in local
 * mode, or when the endpoint is missing: never a guessed figure. Refreshed after every answer, since answers spend it.
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

/** Requests left = the tighter of today's cap and the event cap. */
export function budgetLeft(b: Budget): { left: number; cap: number } {
  const day = { left: Math.max(0, b.day_cap - b.day_used), cap: b.day_cap }
  const event = { left: Math.max(0, b.event_cap - b.event_used), cap: b.event_cap }
  return event.left < day.left ? event : day
}

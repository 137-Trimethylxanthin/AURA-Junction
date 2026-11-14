import { describe, expect, it } from "vitest"
import { budgetTotals } from "./budget"
import { CONTRACT_VERSION } from "@/api/contract"

const base = { contract_version: CONTRACT_VERSION, models: "on" as const, day_used: 1, day_cap: 0, event_used: 1, event_cap: 0 }

describe("budgetTotals", () => {
  it("returns running totals when the server reports them", () => {
    expect(budgetTotals({ ...base, model_calls_total: 213, cost_usd_total: 1.36 })).toEqual({ calls: 213, cost: 1.36 })
  })
  it("returns null when missing", () => {
    expect(budgetTotals(base)).toBeNull()
    expect(budgetTotals(null)).toBeNull()
  })
})

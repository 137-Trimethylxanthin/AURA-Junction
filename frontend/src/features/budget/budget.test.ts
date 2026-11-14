import { describe, expect, it } from "vitest"
import { budgetLeft } from "./budget"
import { CONTRACT_VERSION } from "@/api/contract"

describe("budgetLeft", () => {
  it("picks whichever cap has fewer requests remaining", () => {
    expect(
      budgetLeft({
        contract_version: CONTRACT_VERSION,
        models: "on",
        day_used: 10,
        day_cap: 350,
        event_used: 920,
        event_cap: 1000,
      })
    ).toEqual({ left: 80, cap: 1000 })

    expect(
      budgetLeft({
        contract_version: CONTRACT_VERSION,
        models: "on",
        day_used: 340,
        day_cap: 350,
        event_used: 500,
        event_cap: 1000,
      })
    ).toEqual({ left: 10, cap: 350 })
  })

  it("clamps negative remaining counts to zero", () => {
    expect(
      budgetLeft({
        contract_version: CONTRACT_VERSION,
        models: "on",
        day_used: 400,
        day_cap: 350,
        event_used: 500,
        event_cap: 1000,
      })
    ).toEqual({ left: 0, cap: 350 })
  })
})

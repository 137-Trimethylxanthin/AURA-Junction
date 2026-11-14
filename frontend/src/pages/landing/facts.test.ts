import { describe, expect, it } from "vitest"
import { FACTS, factById, mergeFacts, parseFacts } from "./facts"
import manual from "./facts.manual.json"

describe("facts", () => {
  it("has one entry per id", () => {
    const ids = FACTS.map((f) => f.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
  it("a manual entry replaces the exported one with the same id", () => {
    const m = parseFacts(manual).find((f) => f.id === "check-cost-library")
    expect(m).toBeDefined()
    expect(factById("check-cost-library")).toEqual(m)
  })
  it("keeps exported order and appends manual-only entries", () => {
    const fact = (id: string) => ({ id, label: id, value: "1", scope: "s", method: "m", n: 1, n_unit: "q", measured: "2026-11-14" })
    const a = parseFacts({ facts: [fact("a")] })
    const b = parseFacts({ facts: [fact("b")] })
    expect(mergeFacts(a, b).map((f) => f.id)).toEqual(["a", "b"])
  })
})

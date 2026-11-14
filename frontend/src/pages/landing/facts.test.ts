import { describe, expect, it } from "vitest"
import { FACTS, factById, mergeFacts, parseFacts } from "./facts"
import manual from "./facts.manual.json"

describe("facts", () => {
  it("has one entry per id", () => {
    const ids = FACTS.map((f) => f.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
  it("a manual entry replaces the exported one with the same id", () => {
    const fact = (id: string, value: string) => ({ id, label: id, value, scope: "s", method: "m", n: 1, n_unit: "q", measured: "2026-11-14" })
    const merged = mergeFacts(parseFacts({ facts: [fact("a", "1")] }), parseFacts({ facts: [fact("a", "2")] }))
    expect(merged.map((f) => f.value)).toEqual(["2"])
  })
  it("the check-cost card comes from the exporter, with the 'agreed is not accuracy' caveat", () => {
    expect(parseFacts(manual)).toEqual([])
    expect(factById("check-cost-library")?.caveat).toContain("not a measured accuracy")
  })
  it("keeps exported order and appends manual-only entries", () => {
    const fact = (id: string) => ({ id, label: id, value: "1", scope: "s", method: "m", n: 1, n_unit: "q", measured: "2026-11-14" })
    const a = parseFacts({ facts: [fact("a")] })
    const b = parseFacts({ facts: [fact("b")] })
    expect(mergeFacts(a, b).map((f) => f.id)).toEqual(["a", "b"])
  })
})

import type { DrawingSummary } from "@/api/contract"
import { describe, expect, it } from "vitest"
import { isSheetWithoutPlan, originCounts, originOf, plansFirst, projectsUnderChips } from "./origin"

const s = (id: string, over: Partial<DrawingSummary> = {}): DrawingSummary => ({
  id,
  title: id,
  kind: "raster",
  status: "ready",
  thumbnail: "",
  credit: { text: "Source: x", license: "l", license_name: "n" },
  ...over,
})

describe("library source filter", () => {
  it("takes the origin the server states, else tells bundles and uploads from the summary", () => {
    expect(originOf(s("a", { origin: "us_sheets" }))).toBe("us_sheets")
    expect(originOf(s("b", { source: "upload", origin: "swiss" }))).toBe("uploads")
    expect(originOf(s("c", { kind: "ifc" }))).toBe("ifc")
    expect(originOf(s("d", { credit: { text: "Source: Swiss Dwellings (site1)", license: "l", license_name: "n" } }))).toBe("swiss")
    expect(originOf(s("e", { origin: "nonsense" as never }))).toBe("other")
  })
  it("counts the sources present in a fixed order", () => {
    const list = [s("a", { origin: "cubicasa" }), s("b", { origin: "swiss" }), s("c", { origin: "cubicasa" })]
    expect(originCounts(list)).toEqual([
      { o: "swiss", n: 1 },
      { o: "cubicasa", n: 2 },
    ])
  })
  it("lists sheets without a plan after the plans and hides nothing", () => {
    const list = [s("letter", { n_rooms: 0, n_doors: 0 }), s("bundle"), s("plan", { n_rooms: 7 }), s("doors", { n_rooms: 0, n_doors: 3 })]
    expect(isSheetWithoutPlan(list[0])).toBe(true)
    expect(isSheetWithoutPlan(list[1])).toBe(false) // a bundle states no count: it is a plan
    expect(plansFirst(list).map((d) => d.id)).toEqual(["bundle", "plan", "doors", "letter"])
    // a site plan with one door symbol does not open a source: plans with rooms come before it (US sheets chip)
    const us = [s("site", { n_rooms: 0, n_doors: 1 }), s("blank", { n_rooms: 0, n_doors: 0 }), s("cherokee", { n_rooms: 9, n_doors: 12 })]
    expect(plansFirst(us).map((d) => d.id)).toEqual(["cherokee", "site", "blank"])
    const many = Array.from({ length: 800 }, (_, i) => s(`d${i}`, { n_rooms: i % 3 }))
    expect(plansFirst(many)).toHaveLength(800)
  })
  it("keeps only the projects with a floor that passes the active chips", () => {
    const ifc = { id: "p1", floors: [{ drawing_id: "ifc-a" }, { drawing_id: "ifc-b" }] }
    const swiss = { id: "p2", floors: [{ drawing_id: "sw-1" }, { drawing_id: "sw-2" }] }
    // no chip active: every project stays
    expect(projectsUnderChips([ifc, swiss], null)).toEqual([ifc, swiss])
    // the CubiCasa chip: no project has a CubiCasa floor, so the plans start at the top
    expect(projectsUnderChips([ifc, swiss], new Set(["cc-108"]))).toEqual([])
    expect(projectsUnderChips([ifc, swiss], new Set(["sw-2", "cc-108"]))).toEqual([swiss])
  })
})

import type { DrawingSummary } from "@/api/contract"
import { renderToStaticMarkup } from "react-dom/server"
import { MemoryRouter } from "react-router"
import { describe, expect, it } from "vitest"
import { DrawingCard } from "./DrawingCard"

const page = (n: number): DrawingSummary => ({
  id: `st-cannery--p${n}`,
  title: `20181009 - Cannery Residences · page ${n}`,
  subtitle: `OpeningDesign · CTR · PDF · page ${n} of 16 · 8 rooms`,
  kind: "raster",
  status: "ready",
  thumbnail: "",
  credit: { text: "Source: x", license: "l", license_name: "n" },
})

describe("library card", () => {
  it("wraps title and subtitle to two lines instead of cutting the page number and counts", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <DrawingCard d={page(4)} />
      </MemoryRouter>
    )
    const title = /<h3[^>]*class="([^"]*)"[^>]*data-card-title/.exec(html)?.[1] ?? ""
    const sub = /class="([^"]*)"[^>]*data-card-subtitle/.exec(html)?.[1] ?? ""
    for (const cls of [title, sub]) {
      expect(cls).toContain("line-clamp-2")
      expect(cls.split(" ")).not.toContain("truncate")
    }
    expect(sub).toContain("col-span-full") // the full card width, not only the column beside the badge
    expect(html).toContain("page 4 of 16 · 8 rooms")
  })
})

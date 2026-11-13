import { describe, expect, it } from 'vitest'
import { bundledDrawing } from '@/data/drawing'
import { BADGE, doubleLeaves, placeBadges } from './badges'

const overlaps = (a: [number, number], b: [number, number]) =>
  Math.abs(a[0] - b[0]) < BADGE && Math.abs(a[1] - b[1]) < BADGE

describe('placeBadges', () => {
  for (const [kind, w] of [
    ['door', 449],
    ['door', 568],
    ['door', 700],
    ['window', 449],
    ['washbasin', 449],
  ] as const) {
    it(`no two ${kind} badges overlap at a ${w} px stage`, () => {
      const boxes = bundledDrawing.evidence[kind]
      const { firstLeaf, secondLeaf } = doubleLeaves(boxes)
      const at = placeBadges(boxes, w, w, firstLeaf, secondLeaf)
      const pts = boxes.map((b) => {
        const a = at.get(b.id)!
        return [(a.x / 100) * w + a.dx, (a.y / 100) * w + a.dy] as [number, number]
      })
      const clashes = pts.flatMap((p, i) => pts.slice(i + 1).filter((q) => overlaps(p, q)).map(() => boxes[i].id))
      expect(clashes).toEqual([])
    })
  }

  it('puts the two leaves of a double door on opposite sides', () => {
    const boxes = bundledDrawing.evidence.door
    const { firstLeaf, secondLeaf } = doubleLeaves(boxes)
    expect(firstLeaf.size).toBeGreaterThan(0)
    const at = placeBadges(boxes, 700, 700, firstLeaf, secondLeaf)
    for (const id of firstLeaf) expect(at.get(id)!.dx).toBeLessThan(0)
    for (const id of secondLeaf) expect(at.get(id)!.dx).toBeGreaterThan(0)
  })
})

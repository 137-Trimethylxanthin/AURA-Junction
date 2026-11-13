import type { Box } from '@/api/contract'

/** Badge diameter plus a 2 px gap, in px. */
export const BADGE = 26

export interface BadgeAt {
  /** Anchor in % of the stage. */
  x: number
  y: number
  /** Offset of the badge centre from the anchor, in px. */
  dx: number
  dy: number
}

/** Double doors: boxes with identical rectangles. The first one seen is leaf 1, any repeat is leaf 2. */
export function doubleLeaves(boxes: readonly Box[]): { firstLeaf: Set<string>; secondLeaf: Set<string> } {
  const seen = new Map<string, string>()
  const firstLeaf = new Set<string>()
  const secondLeaf = new Set<string>()
  for (const b of boxes) {
    const k = `${b.x}|${b.y}|${b.w}|${b.h}`
    const prev = seen.get(k)
    if (prev) {
      firstLeaf.add(prev)
      secondLeaf.add(b.id)
    } else seen.set(k, b.id)
  }
  return { firstLeaf, secondLeaf }
}

const SIDE = BADGE / 2 + 1

/**
 * Deterministic badge placement, in reading order: try the box's corners (double-door leaves try
 * just outside their own side) and take the first spot that doesn't overlap a badge already placed;
 * otherwise nudge the first candidate outwards; otherwise keep the first candidate.
 */
export function placeBadges(
  boxes: readonly Box[],
  stageW: number,
  stageH: number,
  firstLeaf: ReadonlySet<string>,
  secondLeaf: ReadonlySet<string>,
): Map<string, BadgeAt> {
  const out = new Map<string, BadgeAt>()
  const placed: [number, number][] = []
  const px = (a: BadgeAt): [number, number] => [(a.x / 100) * stageW + a.dx, (a.y / 100) * stageH + a.dy]
  const free = (a: BadgeAt) => {
    const [cx, cy] = px(a)
    return placed.every(([x, y]) => Math.abs(x - cx) >= BADGE || Math.abs(y - cy) >= BADGE)
  }

  for (const b of boxes) {
    const r = b.x + b.w
    const btm = b.y + b.h
    const cands: BadgeAt[] = firstLeaf.has(b.id)
      ? [
          { x: b.x, y: b.y, dx: -SIDE, dy: 0 },
          { x: b.x, y: btm, dx: -SIDE, dy: 0 },
        ]
      : secondLeaf.has(b.id)
        ? [
            { x: r, y: b.y, dx: SIDE, dy: 0 },
            { x: r, y: btm, dx: SIDE, dy: 0 },
          ]
        : [
            { x: b.x, y: b.y, dx: 0, dy: 0 },
            { x: r, y: b.y, dx: 0, dy: 0 },
            { x: b.x, y: btm, dx: 0, dy: 0 },
            { x: r, y: btm, dx: 0, dy: 0 },
          ]
    const first = cands[0]
    const nudges: [number, number][] = [
      [0, -BADGE],
      [-BADGE, 0],
      [BADGE, 0],
      [0, BADGE],
      [-BADGE, -BADGE],
      [BADGE, -BADGE],
    ]
    const pick =
      cands.find(free) ??
      nudges.map(([dx, dy]) => ({ ...first, dx: first.dx + dx, dy: first.dy + dy })).find(free) ??
      first
    out.set(b.id, pick)
    placed.push(px(pick))
  }
  return out
}

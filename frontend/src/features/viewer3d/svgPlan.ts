import { SVGLoader } from 'three/examples/jsm/loaders/SVGLoader.js'
import { Shape as ShapeCtor, type Shape, type Vector2 } from 'three'

/**
 * Reads the layers of a rebuilt plan.svg (backend/aura/render/svg.py and ifc.py) by their fixed styles:
 *   walls     fill #2f343c (closed polygons; openings are gaps)
 *   windows   fill #ffffff, stroke #374151 (closed polygons inside the wall gaps)
 *   doors     stroke #4b5563, no fill (leaf lines, swing arcs, frames)
 *   fixtures  stroke #6b7280, no fill (basins, WCs, tubs, kitchen; stairs on IFC)
 * Everything else (room outlines, unit outlines, text, background) is ignored: rooms come from the contract.
 * Coordinates stay in SVG px (the contract's Pct indexes the same frame).
 */
export interface SvgPlan {
  width: number
  height: number
  walls: Shape[]
  windows: Shape[]
  /** Closed door sub-paths (frames, thresholds) and open strokes (leaves, arcs). */
  doorLines: Vector2[][]
  fixtureLines: Vector2[][]
  fixtureShapes: Shape[]
}

const norm = (c: unknown) => (typeof c === 'string' ? c.trim().toLowerCase() : '')

const WALL = '#2f343c'
const WINDOW_STROKE = '#374151'
const DOOR = '#4b5563'
const FIXTURE = '#6b7280'

/** True when the text looks like one of our rebuilt plans (has at least one wall polygon). */
export function isRebuiltPlanSvg(text: string): boolean {
  return text.includes('<svg') && text.toLowerCase().includes(`fill="${WALL}"`)
}

export function parsePlanSvg(text: string): SvgPlan {
  const data = new SVGLoader().parse(text)
  const svg = data.xml as unknown as Element
  const vb = (svg.getAttribute?.('viewBox') ?? '').split(/[\s,]+/).map(Number)
  const width = vb.length === 4 && vb[2] > 0 ? vb[2] : Number(svg.getAttribute?.('width')) || 1000
  const height = vb.length === 4 && vb[3] > 0 ? vb[3] : Number(svg.getAttribute?.('height')) || 1000

  const plan: SvgPlan = { width, height, walls: [], windows: [], doorLines: [], fixtureLines: [], fixtureShapes: [] }
  for (const p of data.paths) {
    const st = (p.userData?.style ?? {}) as { fill?: string; stroke?: string }
    const fill = norm(st.fill)
    const stroke = norm(st.stroke)
    if (fill === WALL) {
      plan.walls.push(...SVGLoader.createShapes(p))
    } else if (fill === '#ffffff' && stroke === WINDOW_STROKE) {
      plan.windows.push(...SVGLoader.createShapes(p))
    } else if (stroke === DOOR && (fill === 'none' || fill === '')) {
      for (const sp of p.subPaths) plan.doorLines.push(sp.getPoints(12))
    } else if (stroke === FIXTURE && (fill === 'none' || fill === '')) {
      for (const sp of p.subPaths) {
        const pts = sp.getPoints(16)
        plan.fixtureLines.push(pts)
        if (isClosed(pts) && pts.length >= 4) plan.fixtureShapes.push(new ShapeCtor(pts))
      }
    }
  }
  return plan
}

function isClosed(pts: Vector2[]): boolean {
  if (pts.length < 3) return false
  const a = pts[0]
  const b = pts[pts.length - 1]
  return Math.hypot(a.x - b.x, a.y - b.y) < 0.75
}

/** Shoelace area and perimeter of a shape's outer contour, in the shape's units. */
export function shapeStats(shape: Shape): { area: number; perimeter: number } {
  const pts = shape.getPoints()
  let area = 0
  let perimeter = 0
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i]
    const b = pts[(i + 1) % pts.length]
    area += a.x * b.y - b.x * a.y
    perimeter += Math.hypot(b.x - a.x, b.y - a.y)
  }
  return { area: Math.abs(area) / 2, perimeter }
}

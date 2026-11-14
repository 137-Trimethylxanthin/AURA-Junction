/**
 * Unit display for answers. Answers are metric (m, m², m³); when the question asked for another unit the
 * server adds `converted` ({value, unit: "sq ft", text}). The value is recomputed here from the (possibly
 * user-edited) metric value so a room-size edit keeps the conversion right. Factors are exact by definition.
 */
export type Dim = 'length' | 'area' | 'volume'

const UNITS: Record<string, [Dim, number]> = {
  mm: ['length', 0.001], cm: ['length', 0.01], dm: ['length', 0.1], m: ['length', 1], km: ['length', 1000],
  in: ['length', 0.0254], ft: ['length', 0.3048], yd: ['length', 0.9144], mi: ['length', 1609.344],
  'mm²': ['area', 1e-6], 'cm²': ['area', 1e-4], 'm²': ['area', 1], 'km²': ['area', 1e6], ha: ['area', 1e4],
  a: ['area', 100], 'sq in': ['area', 0.0254 ** 2], 'sq ft': ['area', 0.3048 ** 2], 'sq yd': ['area', 0.9144 ** 2],
  acres: ['area', 4046.8564224],
  'm³': ['volume', 1], 'cm³': ['volume', 1e-6], L: ['volume', 0.001], 'cu ft': ['volume', 0.3048 ** 3],
  'cu in': ['volume', 0.0254 ** 3], 'cu yd': ['volume', 0.9144 ** 3],
}

/** Metric contract unit ('m' | 'm2' | 'm3' | 'm²' …) → display symbol. */
export const METRIC_SYMBOL: Record<string, string> = { m: 'm', m2: 'm²', 'm²': 'm²', m3: 'm³', 'm³': 'm³' }

/** `value` in the metric `from` unit (contract or symbol form) converted to the display `to` symbol, or null. */
export function convertTo(value: number, from: string, to: string): number | null {
  const a = UNITS[METRIC_SYMBOL[from] ?? from]
  const b = UNITS[to]
  if (!a || !b || a[0] !== b[0]) return null
  return (value * a[1]) / b[1]
}

/** The imperial unit for a metric one (area → sq ft, length → ft, volume → cu ft). */
export function imperialOf(metric: string): string | null {
  const u = UNITS[METRIC_SYMBOL[metric] ?? metric]
  if (!u) return null
  return { length: 'ft', area: 'sq ft', volume: 'cu ft' }[u[0]]
}

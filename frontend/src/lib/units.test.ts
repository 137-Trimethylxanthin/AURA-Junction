import { describe, expect, it } from 'vitest'
import { convertTo, imperialOf } from './units'

describe('units', () => {
  it('converts metric answers to the requested unit with exact factors', () => {
    expect(convertTo(12, 'm2', 'sq ft')).toBeCloseTo(129.1669, 3)
    expect(convertTo(3, 'm', 'ft')).toBeCloseTo(9.84252, 4)
    expect(convertTo(1, 'm³', 'cu ft')).toBeCloseTo(35.3147, 3)
    expect(convertTo(12, 'm2', 'ft')).toBeNull()
  })
  it('names the imperial unit of a metric one', () => {
    expect(imperialOf('m2')).toBe('sq ft')
    expect(imperialOf('m')).toBe('ft')
  })
})

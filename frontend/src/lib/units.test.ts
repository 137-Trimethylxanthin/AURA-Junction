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

describe('unit preference', () => {
  it('imperial shows areas in sq ft with the metric value beside; metric shows nothing extra', async () => {
    const { unitPref } = await import('./unitPref')
    const { convertedParts } = await import('@/features/chat/view')
    const resp = { answer: { kind: 'area', value: 10, unit: 'm2' } } as unknown as Parameters<typeof convertedParts>[0]
    const d = { value: 10 } as unknown as Parameters<typeof convertedParts>[1]
    unitPref.set('metric')
    expect(convertedParts(resp, d, 'en')).toBeNull()
    unitPref.set('imperial')
    const c = convertedParts(resp, d, 'en')
    expect(c?.value).toEqual(['107.64', 'sq ft'])
    expect(c?.metric).toBe('10.00 m²')
    unitPref.set('metric')
  })
})

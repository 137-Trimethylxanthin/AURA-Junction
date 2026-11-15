import { describe, expect, it } from 'vitest'
import { clampSplit } from './CompareLayer'

describe('compare divider', () => {
  it('reaches both edges so either drawing can be seen whole', () => {
    expect(clampSplit(-5)).toBe(0)
    expect(clampSplit(0)).toBe(0)
    expect(clampSplit(37)).toBe(37)
    expect(clampSplit(100)).toBe(100)
    expect(clampSplit(140)).toBe(100)
  })
})

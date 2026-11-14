import { describe, expect, it } from 'vitest'
import { ask as askLocal } from '@/api/local'
import { bundledDrawing } from '@/data/drawing'
import { emptyToggles } from '@/lib/answer'
import { evidenceCsv } from './csv'

describe('evidenceCsv', () => {
  const base = askLocal({ drawing_id: 'swiss-46639', question: 'How many doors are drawn on this floor plan?' })
  it('lists IFC rows with no position, marked counted without a box', () => {
    const resp = { ...base, evidence: { boxes: [], rooms: [], rows: [{ id: 'g-1', name: 'Wall', value: 3.5 }, { id: '=cmd' }] } }
    const lines = evidenceCsv(resp, emptyToggles(), bundledDrawing).trim().split('\r\n')
    expect(lines[0]).toBe('n,id,subject,room,unit,x,y,w,h,value,state')
    expect(lines[1]).toBe('1,g-1,Wall,,,,,,,3.5,counted_no_box')
    expect(lines[2]).toBe("2,'=cmd,element,,,,,,,,counted_no_box")
  })
  it('a room with no printed area has an empty value and state no_area, never 0', () => {
    const resp = { ...base, evidence: { boxes: [], rooms: [{ room_id: '3.04', included: false, area_m2: null }] } }
    const line = evidenceCsv(resp, emptyToggles(), bundledDrawing).trim().split('\r\n')[1]
    expect(line.endsWith(',,no_area')).toBe(true)
  })
})

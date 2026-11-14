import { beforeEach, describe, expect, it } from 'vitest'
import { myProjects } from '@/api/library'
import type { UploadRow } from './useUploads'
import { bundleFor, defaultGroupName, groupDecision, pendingGroups, type PendingGroup } from './groups'

const row = (id: string, name: string, phase: UploadRow['phase']): UploadRow => ({
  id,
  name,
  delete_token: 't',
  created_at: '2026-11-14T10:00:00Z',
  phase,
})

beforeEach(() => localStorage.clear())

describe('upload as one project', () => {
  it('names the project after the folder, else the common start of the file names', () => {
    expect(defaultGroupName([{ name: 'a.pdf', webkitRelativePath: 'Haus Seefeld/a.pdf' }, { name: 'b.pdf', webkitRelativePath: 'Haus Seefeld/b.pdf' }])).toBe('Haus Seefeld')
    expect(defaultGroupName([{ name: 'Seefeld_EG.pdf' }, { name: 'Seefeld_1OG.pdf' }])).toBe('Seefeld')
    expect(defaultGroupName([{ name: 'north.dxf' }, { name: 'plan2.dxf' }])).toBe('north')
  })

  it('waits while a sheet is still uploading, saves once ready, drops a group left with one sheet', () => {
    const g: PendingGroup = { id: 'g1', title: 'Seefeld', uploadIds: ['up-a', 'up-b'], created_at: '' }
    expect(groupDecision(g, [row('up-a', 'EG.pdf', 'ready'), row('up-b', '1. OG.pdf', 'processing')]).kind).toBe('wait')
    expect(groupDecision(g, [row('up-a', 'EG.pdf', 'ready'), row('up-b', '1. OG.pdf', 'failed')]).kind).toBe('drop')
    const d = groupDecision(g, [row('up-a', 'Seefeld EG.pdf', 'ready'), row('up-b', 'Seefeld 1. OG.pdf', 'ready')])
    expect(d.kind).toBe('save')
    if (d.kind !== 'save') return
    const p = bundleFor(g, d.ready)
    expect(p).toMatchObject({ title: 'Seefeld', auto_matched: false })
    expect(p.floors.map((f) => [f.drawing_id, f.label])).toEqual([
      ['up-a', 'EG'],
      ['up-b', '1. OG'],
    ])
    expect(myProjects.get(p.id)?.floors).toHaveLength(2)
  })

  it('adds new sheets to an existing project instead of making a second one', () => {
    const existing = myProjects.create('Block B', [
      { id: 'up-1', title: 'EG' },
      { id: 'up-2', title: '1. OG' },
    ])
    const g: PendingGroup = { id: 'g2', title: '', uploadIds: ['up-3'], projectId: existing.id, created_at: '' }
    pendingGroups.add(g)
    const d = groupDecision(g, [row('up-3', '2. OG.dxf', 'ready')])
    expect(d.kind).toBe('save')
    if (d.kind === 'save') expect(bundleFor(g, d.ready).floors.map((f) => f.drawing_id)).toEqual(['up-1', 'up-2', 'up-3'])
    expect(myProjects.list()).toHaveLength(1)
  })
})

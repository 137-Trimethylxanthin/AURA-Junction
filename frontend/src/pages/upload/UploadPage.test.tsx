import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DrawingStatusBody } from '@/api/contract'
import fixture from './status-contract.fixture.json'
import { StageTimeline } from './StageTimeline'
import { stageView } from './stages'

const status = vi.hoisted(() => ({ body: null as unknown, health: { ok: true, contract_version: '1.2', store: 'on' } as unknown }))

vi.mock('@/api/client', async (orig) => ({
  ...(await orig<typeof import('@/api/client')>()),
  getHealth: vi.fn(async () => status.health),
  getDrawingStatus: vi.fn(async () => status.body),
  listProjects: vi.fn(async () => []),
}))
vi.mock('@/api/library', async (orig) => ({
  ...(await orig<typeof import('@/api/library')>()),
  apiBase: () => 'http://api.test',
}))

const { default: UploadPage } = await import('./UploadPage')

let mounted: { root: Root; el: HTMLElement }[] = []
async function render(path: string) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const root = createRoot(el)
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/upload/:drawingId" element={<UploadPage />} />
        </Routes>
      </MemoryRouter>,
    )
  })
  // let the first status poll resolve
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
  mounted.push({ root, el })
  return el
}

function seed(id: string, name: string) {
  localStorage.setItem('aura.uploads', JSON.stringify([{ id, name, delete_token: 'tok', created_at: new Date().toISOString() }]))
}

beforeEach(() => {
  localStorage.clear()
  window.HTMLElement.prototype.scrollIntoView = () => {}
})
afterEach(() => {
  for (const m of mounted) {
    act(() => m.root.unmount())
    m.el.remove()
  }
  mounted = []
})

describe('UploadPage', () => {
  it('shows the empty state and the drop zone facts', async () => {
    status.body = null
    const el = await render('/upload')
    expect(el.querySelector('[data-uploads-empty]')).toBeTruthy()
    expect(el.textContent).toContain('Each page of a PDF is read separately.')
    expect(el.textContent).toContain('deleted after 24 hours')
    expect(el.querySelector('[data-mode="full"]')?.textContent).toContain('Full analysis')
  })

  it('a multi-page PDF mid-way: open now, per-page steps, live counts, estimates and ingest cost', async () => {
    const body = fixture.sequence_multipage_pdf_mid_way as DrawingStatusBody
    status.body = body
    seed(body.id, 'house.pdf')
    const el = await render('/upload')
    const card = el.querySelector('[data-outcome="working"]')!
    expect(card).toBeTruthy()
    expect(card.textContent).toContain('Open now')
    expect(el.querySelectorAll('[data-page]')).toHaveLength(3)
    expect(el.querySelector('[data-page="3"]')?.getAttribute('data-outcome')).toBe('working')
    const counts = el.querySelector('[data-upload-counts]')!.textContent!
    expect(counts).toContain('17')
    expect(counts).toContain('Doors (estimate)')
    expect(counts).not.toContain('Rooms (estimate)')
    expect(el.querySelector('[data-ingest]')?.textContent).toContain('14')
    expect(el.querySelector('[data-ingest]')?.textContent).toContain('$0.061')
  })

  it('store_failed explains itself, stays openable and offers Retry', async () => {
    status.body = fixture.variants.store_failed
    seed('up-F1', 'scan.pdf')
    const el = await render('/upload')
    const callout = el.querySelector('[data-callout="store_failed"]')!
    expect(callout.textContent).toContain('could not read this file')
    expect(callout.textContent).toContain('Retry')
    expect(el.textContent).toContain('Open')
  })

  it('quick mode says plainly what is missing', async () => {
    status.health = { ok: true, contract_version: '1.2', store: 'off' }
    status.body = fixture.variants.quick_mode_pdf
    seed('up-Q2', 'plan.pdf')
    const el = await render('/upload/up-Q2')
    expect(el.querySelector('[data-mode="quick"]')).toBeTruthy()
    expect(el.querySelector('[data-callout="quick"]')?.textContent).toContain('not indexed')
    status.health = { ok: true, contract_version: '1.2', store: 'on' }
  })

  it('works against a backend that sends no stages (old statuses)', async () => {
    status.body = { contract_version: '1.2', id: 'up-old', status: 'processing' }
    seed('up-old', 'old.dxf')
    const el = await render('/upload')
    expect(el.querySelector('[data-stage="parsing"]')).toBeTruthy()
    expect(el.textContent).not.toMatch(/\d+ %/) // no invented percentage
  })
})

describe('StageTimeline', () => {
  it('fills the current step only with a real fraction; otherwise an indeterminate sweep', async () => {
    const el = document.createElement('div')
    const root = createRoot(el)
    await act(async () => root.render(<StageTimeline view={stageView('processing', 'parsing')} fraction={0.4} />))
    expect(el.querySelector('[data-state="current"] span[style]')?.getAttribute('style')).toContain('40%')
    await act(async () => root.render(<StageTimeline view={stageView('ready', 'reading')} fraction={null} />))
    expect(el.querySelector('[data-state="current"] .ul-sweep')).toBeTruthy()
    await act(async () => root.render(<StageTimeline view={stageView('ready', 'ready_unindexed')} fraction={null} quickSkips />))
    expect(el.querySelectorAll('[data-state="skipped"]')).toHaveLength(3)
    act(() => root.unmount())
  })
})

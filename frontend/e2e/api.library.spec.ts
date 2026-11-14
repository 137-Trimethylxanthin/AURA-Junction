import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test, type Page, type Request, type Route } from '@playwright/test'
import { ask, composerOf, lastCard, smallTargets, smallText } from './helpers'

/**
 * Library, upload and drawing loading against a faked API (contract 1.1). Runs only in the API build
 * (`npm run e2e:api`, base http://localhost:4174/__api); every backend response is a Playwright route fake.
 */
test.skip(!process.env.AURA_E2E_API, 'needs the API build: run `npm run e2e:api`')

const V = '1.1'
const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

const bundled = JSON.parse(readFileSync(resolve(import.meta.dirname, '../src/data/drawing.swiss-46639.json'), 'utf8'))
const planSvg = readFileSync(resolve(import.meta.dirname, '../public/drawings/swiss-46639/plan.svg'))

const CREDIT = { text: 'Swiss Dwellings v3.0.0, CC BY 4.0', license: 'CC BY 4.0' }
const LIBRARY = [
  {
    id: 'swiss-46639',
    title: 'Plan 3e étage',
    subtitle: 'Site 11420 · 1:200',
    kind: 'cad_dxf',
    status: 'ready',
    source: 'library',
    thumbnail: '/drawings/swiss-46639/plan.svg',
    facts: { gold_total: 38, correct: 38, measured_at: '2026-11-14' },
    credit: CREDIT,
  },
  {
    id: 'cubi-0007',
    title: 'Apartment scan 7',
    kind: 'raster',
    status: 'ready',
    source: 'library',
    thumbnail: '/drawings/swiss-46639/plan.svg',
    credit: { text: 'CubiCasa5k, CC BY-NC 4.0', license: 'CC BY-NC 4.0' },
  },
]

interface Fake {
  list?: (r: Route) => Promise<void>
  upload?: (r: Route, req: Request) => Promise<void>
  /** Status sequence served by GET /drawings/:id/status, one per poll (the last one repeats). */
  statuses?: Record<string, { status: string; progress?: number; error?: string }[]>
  drawings?: Record<string, unknown>
}

async function fakeApi(page: Page, fake: Fake = {}) {
  const seen = { uploads: [] as Request[], deletes: [] as Request[], list: 0 }
  await page.route('**/__api/health', (r) => json(r, 200, { ok: true, contract_version: V }))
  await page.route('**/__api/drawings', (r) => {
    seen.list++
    return (fake.list ?? ((x: Route) => json(x, 200, { contract_version: V, drawings: LIBRARY })))(r)
  })
  await page.route('**/__api/uploads', (r, req) => {
    seen.uploads.push(req)
    return (fake.upload ?? ((x: Route) => json(x, 202, { contract_version: V, drawing_id: 'up-1', delete_token: 'del-tok', status: 'queued' })))(r, req)
  })
  await page.route(/\/__api\/drawings\/[^/]+\/plan\.svg$/, (r) => r.fulfill({ status: 200, contentType: 'image/svg+xml', body: planSvg }))
  await page.route(/\/__api\/drawings\/([^/]+)\/status$/, (r, req) => {
    const id = decodeURIComponent(new URL(req.url()).pathname.split('/').at(-2)!)
    const seq = fake.statuses?.[id]
    if (!seq) return json(r, 404, { contract_version: V, error: 'Unknown drawing', code: 'unknown_drawing' })
    const s = seq.length > 1 ? seq.shift()! : seq[0]
    return json(r, 200, { contract_version: V, id, ...s })
  })
  await page.route(/\/__api\/drawings\/([^/]+)$/, (r, req) => {
    const id = decodeURIComponent(new URL(req.url()).pathname.split('/').at(-1)!)
    if (req.method() === 'DELETE') {
      seen.deletes.push(req)
      return r.fulfill({ status: 204 })
    }
    const seq = fake.statuses?.[id]
    if (seq && seq[0].status !== 'ready')
      return json(r, 409, { contract_version: V, error: 'Still processing', code: 'processing' })
    const d = fake.drawings?.[id]
    if (!d) return json(r, 404, { contract_version: V, error: `Unknown drawing '${id}'`, code: 'unknown_drawing' })
    return json(r, 200, d)
  })
  return seen
}

const dxf = (name = 'plan.dxf', bytes = 2048) => ({ name, mimeType: 'application/dxf', buffer: Buffer.alloc(bytes, 32) })
const png = (name = 'scan.png') => ({ name, mimeType: 'image/png', buffer: Buffer.alloc(1024, 1) })
const uploadInput = (page: Page) => page.locator('[data-upload-input]')
const row = (page: Page, name: string) => page.locator('li[data-upload]').filter({ hasText: name })

test('the library lists GET /drawings with measured facts, the full-set context and credits', async ({ page }) => {
  await fakeApi(page)
  await page.goto('/app')
  const swiss = page.locator('[data-drawing-card="swiss-46639"]')
  await expect(swiss).toContainText('38/38 dataset answers correct')
  await expect(swiss).toContainText('CC BY 4.0')
  await expect(page.locator('[data-drawing-card="cubi-0007"]')).toContainText('CC BY-NC 4.0')
  // No facts in the body → no number on the card.
  await expect(page.locator('[data-drawing-card="cubi-0007"] [data-facts]')).toHaveCount(0)
  await expect(page.locator('[data-full-set]')).toContainText('18,792')
  await expect(page.locator('[data-offline-note]')).toHaveCount(0)
  // Thumbnails are resolved against the API base.
  await expect(swiss.locator('img')).toHaveAttribute('src', /\/__api\/drawings\/swiss-46639\/plan\.svg$/)
})

test('search and the kind filter narrow the list', async ({ page }) => {
  await fakeApi(page)
  await page.goto('/app')
  await expect(page.locator('[data-drawing-card]')).toHaveCount(2)
  await page.getByRole('radio', { name: 'Image' }).click()
  await expect(page.locator('[data-drawing-card]')).toHaveCount(1)
  await expect(page.locator('[data-drawing-card="cubi-0007"]')).toBeVisible()
  await page.getByRole('radio', { name: 'All' }).click()
  await page.getByRole('searchbox', { name: 'Search drawings' }).fill('étage')
  await expect(page.locator('[data-drawing-card]')).toHaveCount(1)
  await page.getByRole('searchbox', { name: 'Search drawings' }).fill('zzz')
  await expect(page.locator('[data-library-empty]')).toBeVisible()
})

test('a failing list shows an error with Retry and still offers the bundled sample', async ({ page }) => {
  let up = false
  const seen = await fakeApi(page, {
    list: (r) => (up ? json(r, 200, { contract_version: V, drawings: LIBRARY }) : json(r, 500, { error: 'down' })),
  })
  await page.goto('/app')
  await expect(page.locator('[data-library-error]')).toBeVisible()
  await expect(page.locator('[data-drawing-card="swiss-46639"]')).toContainText('Bundled sample')
  up = true
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.locator('[data-library-error]')).toHaveCount(0)
  await expect(page.locator('[data-drawing-card]')).toHaveCount(2)
  expect(seen.list).toBe(2)
})

test('a DXF upload shows progress, polls until ready, opens, and can be deleted with its token', async ({ page }) => {
  const seen = await fakeApi(page, {
    statuses: {
      'up-1': [{ status: 'queued' }, { status: 'processing', progress: 0.5 }, { status: 'ready' }],
    },
  })
  await page.goto('/app')
  await expect(page.getByText('Uploads are processed on our server')).toBeVisible()
  await uploadInput(page).setInputFiles(dxf())
  await expect(row(page, 'plan.dxf')).toHaveAttribute('data-upload', 'ready')
  expect(seen.uploads).toHaveLength(1)
  expect(seen.uploads[0].headers()['x-aura-presenter']).toBeUndefined()
  expect(seen.uploads[0].headers()['content-type']).toMatch(/^multipart\/form-data/)
  // The delete token is kept in this browser only.
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('aura.uploads') ?? '[]'))
  expect(stored).toMatchObject([{ id: 'up-1', name: 'plan.dxf', delete_token: 'del-tok' }])
  await expect(row(page, 'plan.dxf').getByRole('link', { name: 'Open' })).toHaveAttribute('href', '/d/up-1')

  await row(page, 'plan.dxf').getByRole('button', { name: 'Delete plan.dxf' }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click()
  await expect(row(page, 'plan.dxf')).toHaveCount(0)
  expect(seen.deletes).toHaveLength(1)
  expect(seen.deletes[0].headers()['x-delete-token']).toBe('del-tok')
  expect(await page.evaluate(() => localStorage.getItem('aura.uploads'))).toBe('[]')
})

test('drag and drop uploads a DXF too', async ({ page }) => {
  const seen = await fakeApi(page, { statuses: { 'up-1': [{ status: 'processing', progress: 0.2 }] } })
  await page.goto('/app')
  const dt = await page.evaluateHandle(() => {
    const d = new DataTransfer()
    d.items.add(new File(['0\nSECTION\n'], 'dropped.dxf', { type: 'application/dxf' }))
    return d
  })
  const zone = page.locator('[data-dropzone]')
  await zone.dispatchEvent('dragover', { dataTransfer: dt })
  await expect(zone).toHaveAttribute('data-over', 'true')
  await zone.dispatchEvent('drop', { dataTransfer: dt })
  await expect(row(page, 'dropped.dxf')).toHaveAttribute('data-upload', 'processing')
  await expect(row(page, 'dropped.dxf')).toContainText('20 %')
  expect(seen.uploads).toHaveLength(1)
})

test('client-side checks: images need presenter access, oversize and unknown types are refused without a request', async ({ page }) => {
  const seen = await fakeApi(page)
  await page.goto('/app')
  await uploadInput(page).setInputFiles(png())
  await expect(page.locator('[data-upload-rejected]')).toContainText('Needs CAD data. Image-only drawings are a model estimate')
  await uploadInput(page).setInputFiles(dxf('huge.dxf', 20 * 1024 * 1024 + 1))
  await expect(page.locator('[data-upload-rejected]')).toContainText('at most 20 MB')
  await uploadInput(page).setInputFiles({ name: 'plan.dwg', mimeType: 'application/acad', buffer: Buffer.alloc(10) })
  await expect(page.locator('[data-upload-rejected]')).toContainText('Only DXF')
  expect(seen.uploads).toHaveLength(0)
})

test('with a presenter link, a PNG upload is sent with X-AURA-Presenter', async ({ page }) => {
  const seen = await fakeApi(page, { statuses: { 'up-1': [{ status: 'processing' }] } })
  await page.goto('/app?presenter=pres-tok')
  await expect(page).toHaveURL('/app')
  await expect(page.locator('[data-presenter="on"]')).toBeVisible()
  await uploadInput(page).setInputFiles(png())
  await expect(row(page, 'scan.png')).toHaveAttribute('data-upload', 'processing')
  expect(seen.uploads[0].headers()['x-aura-presenter']).toBe('pres-tok')
  await page.locator('[data-presenter="on"]').getByRole('button', { name: 'Turn off' }).click()
  await expect(page.locator('[data-presenter="off"]')).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('aura.presenter'))).toBeNull()
})

test('a server refusal is reported and nothing is stored', async ({ page }) => {
  await fakeApi(page, {
    upload: (r) => json(r, 429, { contract_version: V, error: 'Slow down', code: 'rate_limited' }),
  })
  await page.goto('/app')
  await uploadInput(page).setInputFiles(dxf())
  // The server says which limit was hit; its words are shown.
  await expect(page.getByText('Slow down')).toBeVisible()
  await expect(page.locator('li[data-upload]')).toHaveCount(0)
  expect(await page.evaluate(() => localStorage.getItem('aura.uploads'))).toBeNull()
})

test('opening an upload that is still processing polls, then shows the workspace', async ({ page }) => {
  await fakeApi(page, {
    statuses: { 'up-9': [{ status: 'processing', progress: 0.4 }, { status: 'processing', progress: 0.8 }, { status: 'ready' }] },
    drawings: { 'up-9': { ...bundled, id: 'up-9', title: 'My upload', contract_version: V, kind: 'cad_dxf', source: 'upload' } },
  })
  await page.goto('/d/up-9')
  await expect(page.locator('[data-drawing-load="processing"]')).toBeVisible()
  await expect(composerOf(page)).toBeVisible({ timeout: 10_000 })
  await expect(page).toHaveTitle('My upload · AURA')
  // The plan image comes from the API base.
  await expect(page.locator('[data-plan-stage] img').first()).toHaveAttribute('src', /\/__api\/drawings\/swiss-46639\/plan\.svg$/)
})

test('a failed upload explains why; an unknown id says not found', async ({ page }) => {
  await fakeApi(page, { statuses: { 'up-bad': [{ status: 'failed', error: 'No entities in the DXF' }] } })
  await page.goto('/d/up-bad')
  await expect(page.locator('[data-drawing-load="failed"]')).toContainText('No entities in the DXF')
  await page.goto('/d/nobody')
  await expect(page.locator('[data-drawing-load="not_found"]')).toBeVisible()
})

test('the populated library keeps 12 px text and 40 px targets', async ({ page }) => {
  await fakeApi(page, { statuses: { 'up-1': [{ status: 'processing', progress: 0.3 }] } })
  await page.goto('/app?presenter=p')
  await uploadInput(page).setInputFiles(png())
  await expect(row(page, 'scan.png')).toHaveAttribute('data-upload', 'processing')
  await uploadInput(page).setInputFiles({ name: 'a.dwg', mimeType: 'application/acad', buffer: Buffer.alloc(4) })
  await expect(page.locator('[data-upload-rejected]')).toBeVisible()
  expect(await smallText(page)).toEqual([])
  expect(await smallTargets(page)).toEqual([])
})

test('when the session fell back to local data, an uploaded drawing is never answered from the bundled one', async ({ page }) => {
  await fakeApi(page, {
    statuses: { 'up-7': [{ status: 'ready' }] },
    drawings: { 'up-7': { ...bundled, id: 'up-7', title: 'Other plan', contract_version: V } },
  })
  await page.route('**/__api/health', (r) => r.abort('connectionrefused'))
  await page.goto('/d/up-7')
  await expect(composerOf(page)).toBeVisible()
  await ask(page, 'How many windows are drawn on this floor plan?')
  await expect(lastCard(page).getByText('Couldn’t answer this one')).toBeVisible()
  await expect(lastCard(page).locator('[data-answer-value]')).toHaveCount(0)
})

test('an IFC drawing: notes are not read as "drawn twice", the licence id stays hidden, and dead tools are absent', async ({ page }) => {
  const ifc = {
    ...bundled,
    id: 'ifc-1',
    title: 'FZK-Haus · Erdgeschoss',
    contract_version: V,
    kind: 'ifc',
    images: { rebuilt: '/drawings/swiss-46639/plan.svg' },
    rooms: bundled.rooms.map((r: Record<string, unknown>) => ({ ...r, rect: undefined })),
    credit: {
      text: 'FZK-Haus: KIT IFC Examples',
      license: 'LicenseRef-KIT-unrestricted',
      license_name: 'KIT IFC Examples: for unrestricted use',
      url: 'https://www.ifcwiki.org/',
    },
  }
  await fakeApi(page, { statuses: { 'ifc-1': [{ status: 'ready' }] }, drawings: { 'ifc-1': ifc } })
  await page.route('**/__api/ask', (r) =>
    json(r, 200, {
      contract_version: V,
      answer: { kind: 'count', value: 2, subject: 'window' },
      evidence: {
        boxes: [
          { id: 'win-01', x: 11.7, y: 5.5, w: 16.7, h: 2.5, subject: 'window', note: 'IFC 1TAI4ouKX4Xx4lBDZIu5qM' },
          { id: 'win-02', x: 39.1, y: 5.5, w: 16.7, h: 2.5, subject: 'window', note: 'IFC 2Q9w7oRXP249jBobEMVqlZ' },
        ],
        rooms: [],
      },
      detected_lang: 'en',
      canonical_en: 'How many windows are on this storey',
      scope: { kind: 'floor' },
      method: 'cad_geometry',
      check: { method: 'decisions_api', status: 'sample', image: 'none' },
      trace: [{ id: 'q', title: 'Question', tag: 'API', body: 'fake', kind: 'info' }],
      calls: 0,
      cost_usd: 0,
      latency_ms: 4,
      source: 'api',
    }),
  )
  await page.goto('/d/ifc-1')
  await expect(composerOf(page)).toBeVisible()
  // No dead controls: no rooms with a rectangle → no What-if; no original image → no Compare.
  await expect(page.getByRole('radio', { name: 'What-if sizes' })).toHaveCount(0)
  await expect(page.locator('[aria-label="Compare with original"]')).toHaveCount(0)
  // The licence reads as words, never as the LicenseRef id.
  await expect(page.getByText('KIT IFC Examples: for unrestricted use')).toBeVisible()
  await expect(page.getByText('LicenseRef-KIT-unrestricted')).toHaveCount(0)
  const box = composerOf(page)
  await box.fill('How many windows are on this storey?')
  await box.press('Enter')
  const card = page.locator('article[data-turn]').last()
  await expect(card.locator('[data-answer-value]')).toHaveAttribute('data-answer-value', '2')
  await expect(card.getByRole('button', { name: 'Show Window 1 on the plan' })).toBeVisible()
  await expect(card.locator('[data-evidence-note]').first()).toHaveText('IFC 1TAI4ouKX4Xx4lBDZIu5qM')
  await expect(page.getByText(/drawn twice|second copy/)).toHaveCount(0)
  await expect(card.locator('[data-chip="check"]')).toHaveText('No independent image to check against')
})

test('a #presenter= link turns presenter access on and leaves the address clean; other anchors are kept', async ({ page }) => {
  await fakeApi(page, {})
  await page.goto('/app#presenter=frag-tok')
  await expect(page).toHaveURL('/app')
  await expect(page.locator('[data-presenter="on"]')).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('aura.presenter'))).toBe('frag-tok')
  await page.goto('/app?presenter=q-tok#how')
  await expect(page).toHaveURL('/app#how')
  expect(await page.evaluate(() => localStorage.getItem('aura.presenter'))).toBe('q-tok')
})

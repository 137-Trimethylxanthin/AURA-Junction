import { expect, test, type Page, type Route } from '@playwright/test'
import { composerOf, lastCard, open } from './helpers'

/**
 * Worst-case content must stay inside the laptop layout (review2-break round 2): long tool names in the streaming
 * steps, long printed-text answers, IFC rows without boxes, German card actions, a 300-character rewrite block,
 * and library rows with a missing thumbnail or a very long title. Runs in the API build only, with every call faked.
 * Set AURA_SHOTS=<dir> to also save a screenshot of each case.
 */
test.skip(!process.env.AURA_E2E_API, 'needs the API build: run `npm run e2e:api`')

const V = '1.1'
const SHOTS = process.env.AURA_SHOTS
type Frame = { event: string; data: unknown; delay?: number }

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

async function fakeChat(page: Page, frames: Frame[]) {
  await page.addInitScript((frames: Frame[]) => {
    const real = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/__api/chat')) return real(input, init)
      const enc = new TextEncoder()
      const stream = new ReadableStream<Uint8Array>({
        start(c) {
          let i = 0
          const next = () => {
            if (i >= frames.length) return
            const f = frames[i++]
            setTimeout(() => {
              c.enqueue(enc.encode(`event: ${f.event}\ndata: ${JSON.stringify(f.data)}\n\n`))
              next()
            }, f.delay ?? 0)
          }
          next()
        },
      })
      return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } })
    }
  }, frames)
  await page.route('**/__api/health', (r) => json(r, 200, { ok: true, contract_version: V }))
  await page.route('**/__api/conversations/*', (r) => json(r, 404, { contract_version: V, error: 'unknown', code: 'not_found' }))
}

const base = {
  contract_version: V,
  scope: { kind: 'floor' },
  method: 'cad_geometry',
  trace: [{ id: 'q', title: 'Question', tag: 'API', body: 'fake', kind: 'info' }],
  calls: 0,
  cost_usd: 0,
  latency_ms: 5,
  source: 'api',
  answer_id: 'a-1',
  conversation_id: 'c-1',
  turn_id: 't-1',
  route: 'template',
}
const answerFrames = (response: unknown, lang = 'en'): Frame[] => [
  { event: 'turn', data: { conversation_id: 'c-1', turn_id: 't-1' } },
  { event: 'understood', data: { detected_lang: lang, canonical_en: 'q', route: 'template' } },
  { event: 'answer', data: { response }, delay: 50 },
  { event: 'done', data: {} },
]

/** No element in the chat sidebar is wider than the sidebar (nothing scrolls sideways or is clipped at the edge). */
async function expectNoSidewaysOverflow(page: Page) {
  const out = await page.locator('aside[aria-label="Drawing agent"]').evaluate((aside) => {
    const edge = aside.getBoundingClientRect().right + 1
    const scrollers = [aside, ...aside.querySelectorAll<HTMLElement>('*')].filter(
      // Only the sidebar's own vertical scrollers count; a row built to scroll sideways (overflow-x-auto) is by design.
      (el) => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX === 'auto' && !el.classList.contains('overflow-x-auto'),
    )
    // Content cut off at a clipping box (or the sidebar edge) without an ellipsis. Rows built to scroll sideways are skipped.
    const clipOf = (el: HTMLElement) => {
      for (let a = el.parentElement; a && aside.contains(a); a = a.parentElement)
        if (getComputedStyle(a).overflowX !== 'visible') return a
      return aside
    }
    const past = [...aside.querySelectorAll<HTMLElement>('button, a, p, span, li, textarea')].filter((el) => {
      const r = el.getBoundingClientRect()
      if (r.width === 0) return false
      const clip = clipOf(el)
      if (clip.classList.contains('overflow-x-auto') || el.closest('.overflow-x-auto')) return false
      const limit = Math.min(edge, clip.getBoundingClientRect().right + 1)
      if (r.right <= limit) return false
      // Text that ends in an ellipsis inside its own box is a deliberate truncation, not a clip.
      return !(getComputedStyle(el).textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth)
    })
    return { scrollers: scrollers.map((e) => e.className.slice(0, 60)), past: past.slice(0, 5).map((e) => e.outerHTML.slice(0, 120)) }
  })
  expect(out.scrollers).toEqual([])
  expect(out.past).toEqual([])
}

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}-${page.viewportSize()?.width}.png` })
}

/** Drag the split handle to the right until the sidebar is at its minimum width. */
async function minSidebar(page: Page) {
  const handle = page.locator('[data-slot="resizable-handle"]').first()
  const b = await handle.boundingBox()
  if (!b) return
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2)
  await page.mouse.down()
  await page.mouse.move(b.x + 400, b.y + b.height / 2, { steps: 8 })
  await page.mouse.up()
}

const SIZES = [
  { width: 1440, height: 900 },
  { width: 1366, height: 768 },
]

test.describe('laptop overflow', () => {
  test.skip(({ viewport }) => (viewport?.width ?? 0) < 1300, 'laptop layouts only')

  for (const size of SIZES) {
    test(`long tool names in streaming steps stay in the sidebar at ${size.width}`, async ({ page }) => {
      await page.setViewportSize(size)
      const node = {
        id: 'agent-1',
        parent: 'q',
        title: 'Zählt alle Objekte einer Art auf dem gesamten Grundriss einschliesslich der Nebenräume und Terrassen',
        tag: 'agent · flash-lite',
        body: '',
        kind: 'model',
        tool: { name: 'count_objects_with_a_really_long_internal_name_that_never_ends_v2_beta', args: {}, result_id: 'r', ms: 3 },
      }
      await fakeChat(page, [
        { event: 'turn', data: { conversation_id: 'c-1', turn_id: 't-1' } },
        { event: 'understood', data: { detected_lang: 'de', canonical_en: 'How many places to sleep are there in the whole flat including every room and terrace'.repeat(2), route: 'agent' } },
        { event: 'tool_call', data: { node } },
      ])
      await open(page)
      await minSidebar(page)
      await composerOf(page).fill('Wie viele Schlafplätze gibt es?')
      await composerOf(page).press('Enter')
      await expect(lastCard(page).locator('[data-stream-node="agent-1"]')).toBeVisible()
      await expectNoSidewaysOverflow(page)
      await shot(page, 'stream-long-tool-de-min')
    })

    test(`a long printed-text answer is body size and clamped at ${size.width}`, async ({ page }) => {
      await page.setViewportSize(size)
      const value = 'EI30 selbstschliessend, rauchdicht nach SN EN 1634-3, mit Obentürschliesser und Panikfunktion gemäss VKF-Brandschutzrichtlinie; '.repeat(3)
      const box = { id: 'text-01', x: 40, y: 40, w: 10, h: 3, label: 'Text', subject: 'fire_rating', note: value }
      await fakeChat(page, answerFrames({ ...base, answer: { kind: 'text', value, subject: 'fire_rating' }, evidence: { boxes: [box], rooms: [] }, detected_lang: 'de', canonical_en: 'Fire rating of the entrance door' }, 'de'))
      await open(page)
      await composerOf(page).fill('Welche Brandschutzklasse hat die Eingangstür?')
      await composerOf(page).press('Enter')
      const v = lastCard(page).locator('[data-answer-value]')
      await expect(v).toBeVisible()
      expect((await v.boundingBox())!.height).toBeLessThan(200)
      await expect(lastCard(page).getByRole('button', { name: 'Alles anzeigen' })).toBeVisible()
      await expectNoSidewaysOverflow(page)
      await shot(page, 'text-long-de')
    })

    test(`IFC rows are listed, marked without a box, at ${size.width}`, async ({ page }) => {
      await page.setViewportSize(size)
      const rows = Array.from({ length: 60 }, (_, i) => ({ id: `3cUkl32yn9qRSPvBJVyWYp${i}`, name: `Basic Wall:Interior - 138mm Partition (1-hr) ${i + 1}`, value: 4.25 + i }))
      await fakeChat(page, answerFrames({ ...base, answer: { kind: 'quantity', value: 412.5, unit: 'm' }, evidence: { boxes: [], rooms: [], rows }, canonical_en: 'Total wall length', detected_lang: 'en' }))
      await open(page)
      await composerOf(page).fill('What is the total wall length on this storey?')
      await composerOf(page).press('Enter')
      const card = lastCard(page)
      await expect(card.locator('[data-evidence-kind="element"]').first()).toBeVisible()
      await expect(card.locator('[data-evidence-kind="element"]')).toHaveCount(60)
      await expect(card.locator('[data-evidence-kind="element"]').first()).toContainText('no box on the plan')
      await expectNoSidewaysOverflow(page)
      await shot(page, 'ifc-rows')
    })

    for (const [lang, q] of [['de', 'Welche Brandschutzklasse hat die Eingangstür?'], ['fi', 'Mikä on ulko-oven palonkestävyysluokka?']] as const)
      test(`${lang} card actions fit at the minimum sidebar width at ${size.width}`, async ({ page }) => {
        await page.setViewportSize(size)
        const abst = { ...base, answer: { kind: 'abstain', reason: 'not_on_drawing', where_to_find: 'Brandschutzplan' }, evidence: { boxes: [], rooms: [] }, method: 'none', detected_lang: lang, canonical_en: 'q' }
        // Two cards: the older one is inactive and shows both text actions when they fit.
        await fakeChat(page, answerFrames(abst, lang))
        await open(page)
        await minSidebar(page)
        for (let i = 0; i < 2; i++) {
          await composerOf(page).fill(q)
          await composerOf(page).press('Enter')
          await expect(page.locator('article[data-turn] [data-abstain]')).toHaveCount(i + 1)
        }
        await expectNoSidewaysOverflow(page)
        await shot(page, `abstain-${lang}-min`)
      })
  }
})

test.describe('rewrite worst case', () => {
  test.skip(({ viewport }) => (viewport?.width ?? 0) < 1300, 'laptop layouts only')
  for (const size of SIZES) {
    test(`a 300-character rewrite block is shown whole at ${size.width}`, async ({ page }) => {
      await page.setViewportSize(size)
      const question =
        'Wie gross ist die gesamte Nettowohnfläche der Wohnung A einschliesslich aller Zimmer, Bäder, Küchen und Flure, aber ohne Terrassen, Balkone und Loggien, und welche Räume müssen dabei gegebenenfalls zusätzlich berücksichtigt werden, wenn die Abstellräume ebenfalls berücksichtigt werden sollen, bitte genau?'
      const rewrite = {
        contract_version: V,
        drawing_id: 'swiss-46639',
        detected_lang: 'de',
        blocks: [{ question, intent: 'area', scope: { kind: 'unit', unit: 'A' }, scope_exists: true, routable: true }],
        not_answerable: [],
        calls: 1,
        cost_usd: 0.0001,
        tokens: 900,
        model: 'gemini-flash-lite',
      }
      await page.route('**/__api/health', (r) => json(r, 200, { ok: true, contract_version: V }))
      await page.route('**/__api/rewrite', (r) => json(r, 200, rewrite))
      await open(page)
      await expect(page.getByRole('status').filter({ hasText: 'Live API' })).toBeVisible()
      await composerOf(page).fill('ok also die wohnung a wie gross ist die eigentlich und auch noch die bäder und was ist mit den terrassen')
      await page.locator('[data-tidy]').first().click()
      const box = page.locator('[data-rewrite-block="1"]').getByRole('textbox')
      await expect(box).toHaveValue(question)
      // The whole sentence is visible: the box is several lines tall and its content does not scroll.
      const h = await box.evaluate((el) => ({ scroll: el.scrollHeight, client: el.clientHeight }))
      expect(h.client).toBeGreaterThan(60)
      expect(h.scroll).toBeLessThanOrEqual(h.client + 2)
      await expectNoSidewaysOverflow(page)
      await shot(page, 'rewrite-300-de')
    })
  }
})

test.describe('library worst cases', () => {
  test.skip(({ viewport }) => (viewport?.width ?? 0) < 1300, 'laptop layouts only')
  const credit = { text: 'Test credit', license: 'CC BY 4.0' }
  const row = (id: string, extra: object) => ({ id, title: `Drawing ${id}`, kind: 'cad_dxf', status: 'ready', thumbnail: '/x.svg', credit, source: 'library', ...extra })

  for (const size of SIZES) {
    test(`one bad row cannot blank the list; long titles keep the badge at ${size.width}`, async ({ page }) => {
      await page.setViewportSize(size)
      const bad = row('bad', {})
      delete (bad as { thumbnail?: string }).thumbnail
      await page.route('**/__api/health', (r) => json(r, 200, { ok: true, contract_version: V }))
      await page.route('**/__api/drawings', (r) =>
        json(r, 200, {
          contract_version: V,
          drawings: [
            row('long', { title: 'Wohnüberbauung Hardturmstrasse Haus B Erdgeschoss bis Attikageschoss Ausführungsplanung Revision 14 '.repeat(2) }),
            bad,
            row('failed', { status: 'failed', error: 'x'.repeat(240) }),
            row('ok', {}),
          ],
        }),
      )
      await page.route('**/x.svg', (r) => r.fulfill({ status: 404, body: '' }))
      await page.goto('/app')
      await expect(page.locator('[data-drawing-card="ok"]')).toBeVisible()
      await expect(page.locator('[data-library-dropped]')).toContainText('1 drawing')
      await expect(page.locator('[data-library-error]')).toHaveCount(0)
      const card = page.locator('[data-drawing-card="long"]')
      const badge = card.locator('[data-slot="badge"]')
      await expect(badge).toBeVisible()
      const cb = (await card.boundingBox())!
      const bb = (await badge.boundingBox())!
      expect(bb.x + bb.width).toBeLessThanOrEqual(cb.x + cb.width)
      const failed = page.locator('[data-drawing-card="failed"] .text-destructive')
      const fb = (await failed.boundingBox())!
      const fc = (await page.locator('[data-drawing-card="failed"]').boundingBox())!
      expect(fb.x + fb.width).toBeLessThanOrEqual(fc.x + fc.width + 1)
      await expect(page.locator('[data-drawing-card="ok"] [data-no-thumbnail]')).toBeVisible()
      await shot(page, 'library-worst')
    })

    test(`ten dropped files: five queued, the rest named; long names wrap at ${size.width}`, async ({ page }) => {
      await page.setViewportSize(size)
      const posts: string[] = []
      await page.route('**/__api/health', (r) => json(r, 200, { ok: true, contract_version: V }))
      await page.route('**/__api/drawings', (r) => json(r, 200, { contract_version: V, drawings: [] }))
      await page.route('**/__api/uploads', (r) => (posts.push('x'), json(r, 422, { detail: [{ msg: 'fake: not accepted' }] })))
      await page.goto('/app')
      const long = `Grundriss_${'Erdgeschoss_Haus_B_Revision_'.repeat(8)}.dwg`
      const files = [
        ...Array.from({ length: 9 }, (_, i) => ({ name: `plan-${i}.dxf`, mimeType: 'application/dxf', buffer: Buffer.from('0\nSECTION\n') })),
        { name: long, mimeType: 'application/octet-stream', buffer: Buffer.from('x') },
      ]
      await page.locator('[data-upload-input]').setInputFiles(files)
      const alert = page.locator('[data-upload-rejected]')
      await expect(alert).toContainText('5 files not uploaded')
      await expect(alert).toContainText('only 5 files are uploaded at a time; drop them again')
      await expect.poll(() => posts.length).toBe(5)
      const ab = (await alert.boundingBox())!
      for (const li of await alert.locator('li').all()) {
        const b = (await li.boundingBox())!
        expect(b.x + b.width).toBeLessThanOrEqual(ab.x + ab.width + 1)
      }
      await shot(page, 'upload-10')
    })
  }
})

import { expect, test, type Page, type Route } from '@playwright/test'
import { Q, answerValue, ask, composerOf, drawing, lastCard, open, planBoxes } from './helpers'

/**
 * The API path. The app reads VITE_API_URL at BUILD time, so these tests need a build whose API base is
 * http://localhost:4174/__api (same origin as the preview, so no CORS). `e2e/playwright.api.config.ts` builds and
 * serves that variant and sets AURA_E2E_API=1; without it this file is skipped. Every backend response is a
 * Playwright route fake: nothing here talks to a real server.
 */
test.skip(!process.env.AURA_E2E_API, 'needs the API build: run `npx playwright test -c e2e/playwright.api.config.ts`')

const HEALTH = { ok: true, contract_version: '1.0' }

function askResponse(patch: Record<string, unknown> = {}) {
  return {
    contract_version: '1.0',
    answer: { kind: 'count', value: 12, subject: 'window' },
    evidence: { boxes: drawing.evidence.window.map((b) => ({ ...b, subject: 'window' })), rooms: [] },
    detected_lang: 'en',
    canonical_en: 'How many windows are drawn on this floor plan?',
    scope: { kind: 'floor' },
    method: 'cad_geometry',
    gold: { dataset_qid: 'questions.jsonl:103409', value: 12, tolerance: 'exact', match: true },
    trace: [{ id: 'q', title: 'Question', tag: 'API', body: 'from the fake API', kind: 'info' }],
    calls: 0,
    cost_usd: 0,
    latency_ms: 7,
    source: 'api',
    ...patch,
  }
}

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

interface Fake {
  health?: (route: Route) => Promise<void>
  ask?: (route: Route) => Promise<void>
}

/** Route the fake backend and count the calls it receives. */
async function fakeApi(page: Page, fake: Fake) {
  const calls = { health: 0, ask: 0 }
  await page.route('**/__api/health', (route) => {
    calls.health++
    return (fake.health ?? ((r: Route) => json(r, 200, HEALTH)))(route)
  })
  await page.route('**/__api/ask', (route) => {
    calls.ask++
    return (fake.ask ?? ((r: Route) => json(r, 200, askResponse())))(route)
  })
  return calls
}

const badge = (page: Page, text: string) => page.getByRole('status').filter({ hasText: text })
const errorCard = (page: Page) => lastCard(page).getByText('Couldn’t answer this one')

test('a healthy API shows the Live API badge and renders its answer', async ({ page }) => {
  const calls = await fakeApi(page, {})
  await open(page)
  await expect(badge(page, 'Live API')).toBeVisible()
  await ask(page, Q.windows)
  expect(await answerValue(page)).toBe('12')
  await expect(planBoxes(page)).toHaveCount(12)
  await expect(lastCard(page).locator('[data-footer]')).toContainText('API')
  await expect(lastCard(page).locator('[data-footer]')).not.toContainText('local data')
  expect(calls.ask).toBe(1)
})

test('a 422 with a FastAPI detail shows an error card, no number and no fallback', async ({ page }) => {
  const calls = await fakeApi(page, { ask: (r) => json(r, 422, { detail: [{ loc: ['body', 'question'], msg: 'field required' }] }) })
  await open(page)
  await ask(page, Q.windows)
  await expect(errorCard(page)).toBeVisible()
  await expect(lastCard(page).locator('[data-answer-value]')).toHaveCount(0)
  await expect(planBoxes(page)).toHaveCount(0)
  await expect(badge(page, 'Live API')).toBeVisible()
  await expect(badge(page, 'Local data')).toHaveCount(0)
  expect(calls.ask).toBe(1)
})

test.describe('503 from /ask', () => {
  test('falls back to local with a visible badge and a toast', async ({ page }) => {
    await fakeApi(page, { ask: (r) => json(r, 503, { error: 'busy' }) })
    await open(page)
    await ask(page, Q.windows)
    expect(await answerValue(page)).toBe('12')
    await expect(lastCard(page).locator('[data-footer]')).toContainText('local data')
    await expect(badge(page, 'Local data')).toBeVisible()
    await expect(page.getByText('Backend unreachable. Answering from local data.')).toBeVisible()
  })

  test('every later answer stays local: the API is not asked again, so sources never mix', async ({ page }) => {
    const calls = await fakeApi(page, { ask: (r) => json(r, 503, { error: 'busy' }) })
    await open(page)
    await ask(page, Q.windows)
    await ask(page, Q.doorsFi)
    await ask(page, Q.areaFr)
    expect(calls.ask).toBe(1)
    const footers = await page.locator('article[data-turn] [data-footer]').allTextContents()
    expect(footers).toHaveLength(3)
    // Footers are localised (EN, FI, FR here), so assert on the one token they share: none says "API".
    expect(footers[0]).toContain('local data')
    for (const f of footers) expect(f).not.toMatch(/\bAPI\b/)
  })
})

test.describe('/health failing', () => {
  test('a refused connection answers locally from the first question', async ({ page }) => {
    const calls = await fakeApi(page, { health: (r) => r.abort('connectionrefused') })
    await open(page)
    await expect(badge(page, 'Local data')).toBeVisible()
    await ask(page, Q.windows)
    expect(await answerValue(page)).toBe('12')
    await expect(lastCard(page).locator('[data-footer]')).toContainText('local data')
    expect(calls.ask).toBe(0)
  })

  test('a 500 answers locally and offers a retry', async ({ page }) => {
    const calls = await fakeApi(page, { health: (r) => json(r, 500, { error: 'down' }) })
    await open(page)
    await expect(badge(page, 'Local data')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Retry API' })).toBeVisible()
    await ask(page, Q.windows)
    expect(calls.ask).toBe(0)
  })

  test('Retry API switches to the live API once /health recovers', async ({ page }) => {
    let up = false
    const calls = await fakeApi(page, {
      health: (r) => (up ? json(r, 200, HEALTH) : r.abort('connectionrefused')),
    })
    await open(page)
    await expect(badge(page, 'Local data')).toBeVisible()
    up = true
    await page.getByRole('button', { name: 'Retry API' }).click()
    await expect(badge(page, 'Live API')).toBeVisible()
    await ask(page, Q.windows)
    expect(calls.ask).toBe(1)
    await expect(lastCard(page).locator('[data-footer]')).not.toContainText('local data')
  })
})

test('an /ask response with another contract major shows an error card and no number', async ({ page }) => {
  await fakeApi(page, { ask: (r) => json(r, 200, askResponse({ contract_version: '2.0' })) })
  await open(page)
  await ask(page, Q.windows)
  await expect(errorCard(page)).toBeVisible()
  await expect(lastCard(page)).toContainText('2.0')
  await expect(lastCard(page).locator('[data-answer-value]')).toHaveCount(0)
  await expect(badge(page, 'Local data')).toHaveCount(0)
})

test('an /ask body that breaks the contract shows an error card, not a guess', async ({ page }) => {
  await fakeApi(page, { ask: (r) => json(r, 200, { contract_version: '1.0', answer: { kind: 'count', value: 'twelve' } }) })
  await open(page)
  await ask(page, Q.windows)
  await expect(errorCard(page)).toBeVisible()
  await expect(lastCard(page).locator('[data-answer-value]')).toHaveCount(0)
})

test('the composer stays usable after an error card', async ({ page }) => {
  let n = 0
  await fakeApi(page, { ask: (r) => (++n === 1 ? json(r, 422, { detail: 'nope' }) : json(r, 200, askResponse())) })
  await open(page)
  await ask(page, Q.windows)
  await expect(errorCard(page)).toBeVisible()
  await ask(page, Q.windows)
  expect(await answerValue(page)).toBe('12')
  await expect(composerOf(page)).toBeEnabled()
})

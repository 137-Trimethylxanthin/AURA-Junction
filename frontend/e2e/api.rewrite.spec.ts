import { expect, test, type Page, type Request, type Route } from '@playwright/test'
import { composerOf, drawing, lastCard, open } from './helpers'

/**
 * The rewrite/confirm card with a faked POST /rewrite (contract 1.1). /chat is not faked, so the client falls back
 * to the faked /ask for confirmed questions. Runs in the API build only.
 */
test.skip(!process.env.AURA_E2E_API, 'needs the API build: run `npm run e2e:api`')

const V = '1.1'
const RAMBLING =
  'ok so how many sinks are there in both flats and also bedroom 3.05 how big is it, and is the ceiling high enough for a loft bed'

const REWRITE = {
  contract_version: V,
  drawing_id: 'swiss-46639',
  detected_lang: 'en',
  blocks: [
    { question: 'How many washbasins are drawn on this floor plan?', intent: 'count', subject: 'washbasin', scope: { kind: 'floor' }, scope_exists: true, routable: true },
    { question: 'What is the net floor area of room 3.05?', intent: 'area', scope: { kind: 'room', room_id: '3.05' }, scope_exists: true, routable: true },
    { question: 'How many windows are in apartment Z?', intent: 'count', subject: 'window', scope: { kind: 'unit', unit: 'Z' }, scope_exists: false, routable: false },
  ],
  not_answerable: ['is the ceiling high enough for a loft bed'],
  calls: 1,
  cost_usd: 0.0001,
  tokens: 812,
  model: 'gemini-flash-lite',
}

function answerFor(body: { question: string; scope?: unknown }) {
  const isArea = /area/i.test(body.question)
  return {
    contract_version: V,
    answer: isArea ? { kind: 'area', value: 13.81, unit: 'm2' } : { kind: 'count', value: 5, subject: 'washbasin' },
    evidence: isArea
      ? { boxes: [], rooms: [{ room_id: '3.05', included: true, area_m2: 13.81 }] }
      : { boxes: drawing.evidence.washbasin.map((b) => ({ ...b, subject: 'washbasin' })), rooms: [] },
    detected_lang: 'en',
    canonical_en: body.question,
    scope: body.scope ?? { kind: 'floor' },
    method: 'cad_geometry',
    trace: [{ id: 'q', title: 'Question', tag: 'API', body: 'fake', kind: 'info' }],
    calls: 0,
    cost_usd: 0,
    latency_ms: 5,
    source: 'api',
  }
}

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

async function fakeApi(page: Page, rewrite: (r: Route) => Promise<void> = (r) => json(r, 200, REWRITE)) {
  const seen = { rewrites: [] as Request[], asks: [] as { question: string; scope?: unknown }[] }
  await page.route('**/__api/health', (r) => json(r, 200, { ok: true, contract_version: V }))
  await page.route('**/__api/rewrite', (r, req) => {
    seen.rewrites.push(req)
    return rewrite(r)
  })
  await page.route('**/__api/ask', (r, req) => {
    const body = req.postDataJSON() as { question: string; scope?: unknown }
    seen.asks.push(body)
    return json(r, 200, answerFor(body))
  })
  return seen
}

async function openLive(page: Page) {
  await open(page)
  await expect(page.getByRole('status').filter({ hasText: 'Live API' })).toBeVisible()
}

const card = (page: Page) => page.locator('[data-rewrite-card]')

test('a rambling message is tidied into confirmable questions; nothing is asked until confirmed', async ({ page }) => {
  const seen = await fakeApi(page)
  await openLive(page)
  await composerOf(page).fill(RAMBLING)
  // The local heuristic offers it (no model call to decide).
  const offer = page.locator('[data-tidy="suggested"]')
  await expect(offer).toBeVisible()
  expect(seen.rewrites).toHaveLength(0)
  await offer.click()
  await expect(card(page)).toHaveAttribute('data-rewrite-card', 'model')
  expect(seen.rewrites).toHaveLength(1)
  expect(seen.rewrites[0].postDataJSON()).toEqual({ drawing_id: 'swiss-46639', text: RAMBLING })
  await expect(card(page).locator('[data-rewrite-original]')).toContainText('ok so how many sinks')
  // Verb · object · scope, and the sentence, per block.
  const b1 = card(page).locator('[data-rewrite-block="1"]')
  await expect(b1).toContainText('Count')
  await expect(b1).toContainText('washbasins')
  await expect(b1).toContainText('whole floor')
  await expect(b1.getByRole('textbox', { name: 'Question 1' })).toHaveValue(REWRITE.blocks[0].question)
  await expect(card(page).locator('[data-rewrite-block="2"]')).toContainText('Room 3.05')
  // The unplaceable one is greyed out and cannot be asked.
  const b3 = card(page).locator('[data-rewrite-block="3"]')
  await expect(b3).toHaveAttribute('data-routable', 'false')
  await expect(b3.getByRole('button', { name: 'Ask this one' })).toBeDisabled()
  await expect(card(page).locator('[data-rewrite-not-here]')).toContainText('loft bed')
  // The model use is stated and counted.
  await expect(card(page).locator('[data-rewrite-cost]')).toHaveText('Gemini rewrote this · 1 request · $0.0001')
  await expect(page.getByRole('button', { name: /^Session: 1 model call, \$0\.0001/ })).toBeVisible()
  expect(seen.asks).toHaveLength(0)

  // Enter in block 1 asks it, with the block's own scope.
  await b1.getByRole('textbox').press('Enter')
  await expect(lastCard(page).locator('[data-answer-value]')).toHaveAttribute('data-answer-value', '5')
  expect(seen.asks[0]).toMatchObject({ question: REWRITE.blocks[0].question, scope: { kind: 'floor' } })
  await expect(b1).toHaveAttribute('data-asked', 'true')

  // Ask all asks what is left (the routable block 2 only), then the card goes away.
  await card(page).getByRole('button', { name: 'Ask all' }).click()
  await expect(card(page)).toHaveCount(0)
  expect(seen.asks.map((a) => a.question)).toEqual([REWRITE.blocks[0].question, REWRITE.blocks[1].question])
  expect(seen.asks[1].scope).toEqual({ kind: 'room', room_id: '3.05' })
})

test('keyboard: an edited block is asked as edited; Edit moves a sentence to the composer; Escape dismisses', async ({ page }) => {
  const seen = await fakeApi(page)
  await openLive(page)
  await composerOf(page).fill(RAMBLING)
  await page.locator('[data-tidy="suggested"]').click()
  const b1 = card(page).locator('[data-rewrite-block="1"]')
  await expect(b1.getByRole('textbox')).toBeFocused()
  await b1.getByRole('textbox').fill('How many washbasins are in apartment A?')
  await card(page).locator('[data-rewrite-block="2"]').getByRole('button', { name: 'Edit' }).click()
  await expect(composerOf(page)).toHaveValue(REWRITE.blocks[1].question)
  await expect(card(page).locator('[data-rewrite-block]')).toHaveCount(2)
  await b1.getByRole('textbox').press('Enter')
  await expect(lastCard(page).locator('[data-answer-value]')).toBeVisible()
  expect(seen.asks[0].question).toBe('How many washbasins are in apartment A?')
  await card(page).getByRole('textbox', { name: 'Question 2' }).press('Escape')
  await expect(card(page)).toHaveCount(0)
  expect(seen.asks).toHaveLength(1)
})

test('when the model budget is used up, an honest local split is offered instead', async ({ page }) => {
  await fakeApi(page, (r) => json(r, 429, { contract_version: V, error: 'Budget', code: 'budget_exhausted' }))
  await openLive(page)
  await composerOf(page).fill('How many windows? How big is room 3.04?')
  await page.locator('[data-tidy="suggested"]').click()
  await expect(card(page)).toHaveAttribute('data-rewrite-card', 'local')
  await expect(card(page).locator('[data-rewrite-cost]')).toContainText('Split locally, no model')
  await expect(card(page).locator('[data-rewrite-cost]')).toContainText('model budget is used up')
  await expect(card(page).locator('[data-rewrite-block]')).toHaveCount(2)
  await expect(page.getByRole('button', { name: /^Session: 0 model calls/ })).toBeVisible()
})

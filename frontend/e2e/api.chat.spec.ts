import { expect, test, type Page, type Route } from '@playwright/test'
import { Q, composerOf, drawing, lastCard, open, planBoxes } from './helpers'

/**
 * Streaming chat against a faked POST /chat (contract 1.1 SSE). Playwright's route.fulfill cannot stream, so the
 * page's fetch is wrapped for /__api/chat only: it answers with a ReadableStream that releases one scripted frame
 * at a time. Everything else (/health, /conversations) is a normal route fake. Runs in the API build only.
 */
test.skip(!process.env.AURA_E2E_API, 'needs the API build: run `npm run e2e:api`')

const V = '1.1'
type Frame = { event: string; data: unknown; delay?: number }

const boxes = drawing.evidence.window.map((b) => ({ ...b, subject: 'window' }))
const check = {
  method: 'decisions_api',
  status: 'live',
  scope: 'drawn_boxes',
  ranking: ['win-09', ...boxes.map((b) => b.id).filter((id) => id !== 'win-09')],
  least_certain_id: 'win-09',
  anchors_max: 0.11,
  per_box: Object.fromEntries(boxes.map((b) => [b.id, b.id === 'win-09' ? 0.67 : 0.85])),
  image: 'dataset_png',
  calls: 1,
  cost_usd: 0.0014,
  tokens: 13612,
  cached: false,
}
const node = {
  id: 'read',
  parent: 'q',
  title: 'Read the layer',
  tag: 'DXF · window layer',
  body: '12 windows on the DXF window layer.',
  kind: 'exact',
  evidence_ids: boxes.map((b) => b.id),
  tool: { name: 'count_objects', args: { subject: 'window' }, result_id: 'r-1', ms: 4 },
}
const response = {
  contract_version: V,
  answer: { kind: 'count', value: 12, subject: 'window' },
  evidence: { boxes, rooms: [] },
  detected_lang: 'en',
  canonical_en: 'How many windows are drawn on this floor plan',
  scope: { kind: 'floor' },
  method: 'cad_geometry',
  check,
  gold: { dataset_qid: 'questions.jsonl:103425', value: 12, tolerance: 'exact', match: true },
  trace: [{ id: 'q', title: 'Question', tag: 'API', body: 'from the fake stream', kind: 'info' }, node],
  calls: 0,
  cost_usd: 0,
  latency_ms: 9,
  source: 'api',
  answer_id: 'a-1',
  conversation_id: 'c-1',
  turn_id: 't-1',
  route: 'template',
}
const STREAM: Frame[] = [
  { event: 'turn', data: { conversation_id: 'c-1', turn_id: 't-1' } },
  { event: 'understood', data: { detected_lang: 'en', canonical_en: response.canonical_en, route: 'template' }, delay: 300 },
  { event: 'tool_call', data: { node }, delay: 300 },
  { event: 'tool_result', data: { node_id: 'read', evidence: { boxes, rooms: [] }, summary: '12 windows' }, delay: 900 },
  { event: 'check', data: { check }, delay: 600 },
  { event: 'answer', data: { response }, delay: 2500 },
  { event: 'done', data: {} },
]

/** Wrap window.fetch so POST /__api/chat streams `frames`; request bodies are kept in window.__chatBodies. */
async function fakeChat(page: Page, frames: Frame[]) {
  await page.addInitScript((frames: Frame[]) => {
    const w = window as unknown as { __chatBodies: unknown[]; fetch: typeof fetch }
    w.__chatBodies = []
    const real = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/__api/chat')) return real(input, init)
      w.__chatBodies.push(JSON.parse(String(init?.body ?? '{}')))
      const enc = new TextEncoder()
      let timer: ReturnType<typeof setTimeout> | undefined
      const stream = new ReadableStream<Uint8Array>({
        start(c) {
          let i = 0
          const next = () => {
            if (i >= frames.length) return c.close()
            const f = frames[i++]
            timer = setTimeout(() => {
              c.enqueue(enc.encode(`event: ${f.event}\ndata: ${JSON.stringify(f.data)}\n\n`))
              next()
            }, f.delay ?? 0)
          }
          next()
        },
        cancel() {
          clearTimeout(timer)
        },
      })
      init?.signal?.addEventListener('abort', () => clearTimeout(timer))
      return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } })
    }
  }, frames)
}

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

async function fakeApi(page: Page, conversation?: unknown) {
  await page.route('**/__api/health', (r) => json(r, 200, { ok: true, contract_version: V }))
  await page.route('**/__api/conversations/*', (r) =>
    conversation ? json(r, 200, conversation) : json(r, 404, { contract_version: V, error: 'unknown', code: 'bad_request' }),
  )
}

async function send(page: Page, text: string) {
  const box = composerOf(page)
  await box.fill(text)
  await box.press('Enter')
}

test('a streamed answer shows each step live, lights evidence before the card, then settles', async ({ page }) => {
  await fakeChat(page, STREAM)
  await fakeApi(page)
  await open(page)
  await expect(page.getByRole('status').filter({ hasText: 'Live API' })).toBeVisible()
  await send(page, Q.windows)
  const card = lastCard(page)
  // Understood, then the tool call running, then its evidence on the plan, all before the number exists.
  await expect(card.locator('[data-stream-steps]')).toContainText('Understood as')
  await expect(card.locator('[data-stream-node="read"]')).toHaveAttribute('data-state', 'running')
  await expect(card.locator('[data-stream-node="read"]')).toContainText('count_objects')
  await expect(card.locator('[data-stream-node="read"]')).toHaveAttribute('data-state', 'done')
  await expect(planBoxes(page)).toHaveCount(12)
  await expect(card.locator('[data-answer-value]')).toHaveCount(0)
  await expect(card.locator('[data-stream-check]')).toBeVisible()
  // The reasoning tree shows the tool node while the answer is still coming.
  await card.getByRole('button', { name: 'Reasoning tree' }).click()
  await expect(page.locator('[data-trace-panel] [data-trace-node="read"]')).toBeVisible()
  await page.getByRole('button', { name: /Back to chat/ }).click()
  // The answer finalises the card.
  await expect(card.locator('[data-answer-value]')).toHaveAttribute('data-answer-value', '12')
  await expect(card.locator('[aria-busy="true"]')).toHaveCount(0)
  // Ranked check: no "agree" count; the least-certain box is named and marked.
  const chip = card.locator('[data-chip="check"]')
  await expect(chip).toContainText('Gemini checked 12 drawn boxes · least certain #9')
  await expect(page.locator('[data-plan-stage] [data-least-certain]')).toHaveAttribute('data-evidence-id', 'win-09')
  await chip.click()
  await expect(page.locator('[data-plan-stage] [data-evidence-id="win-09"]')).toHaveAttribute('aria-pressed', 'true')
  // Cost: the answer's 0 calls and the check's request are both shown, and the session sums them.
  const footer = card.locator('[data-footer]')
  await expect(footer).toContainText('0 model calls')
  await expect(footer).toContainText('check: 1 request')
  await expect(footer).toContainText('13.6k tokens')
  await expect(footer).toContainText('$0.0014')
  await expect(page.getByRole('button', { name: /^Session: 1 model call, \$0\.0014/ })).toBeVisible()
})

test('a follow-up carries the conversation id, and the conversation is restored after a reload', async ({ page }) => {
  await fakeChat(page, STREAM.map((f) => ({ ...f, delay: 0 })))
  await fakeApi(page, {
    contract_version: V,
    id: 'c-1',
    drawing_id: 'swiss-46639',
    turns: [{ turn_id: 't-1', message: Q.windows, response }],
  })
  await open(page)
  await expect(page.getByRole('status').filter({ hasText: 'Live API' })).toBeVisible()
  await send(page, Q.windows)
  await expect(lastCard(page).locator('[data-answer-value]')).toHaveAttribute('data-answer-value', '12')
  await send(page, 'and doors?')
  await expect(page.locator('article[data-turn]')).toHaveCount(2)
  const bodies = await page.evaluate(() => (window as unknown as { __chatBodies: { conversation_id?: string }[] }).__chatBodies)
  expect(bodies.map((b) => b.conversation_id ?? null)).toEqual([null, 'c-1'])
  expect(await page.evaluate(() => localStorage.getItem('aura.conversation.swiss-46639'))).toBe('c-1')

  await page.reload()
  await expect(lastCard(page).locator('[data-answer-value]')).toHaveAttribute('data-answer-value', '12')
  await expect(page.locator('[data-user-turn]')).toContainText(Q.windows)
  // A fresh start forgets it.
  await page.getByRole('button', { name: 'New conversation' }).click()
  await expect(page.locator('article[data-turn]')).toHaveCount(0)
  expect(await page.evaluate(() => localStorage.getItem('aura.conversation.swiss-46639'))).toBeNull()
})

test('Stop ends a streaming answer without a number', async ({ page }) => {
  await fakeChat(page, [STREAM[0], STREAM[1], { ...STREAM[2], delay: 60_000 }])
  await fakeApi(page)
  await open(page)
  await expect(page.getByRole('status').filter({ hasText: 'Live API' })).toBeVisible()
  await send(page, Q.windows)
  const card = lastCard(page)
  await expect(card.locator('[data-stream-steps]')).toContainText('Understood as')
  await card.getByRole('button', { name: 'Stop' }).click()
  await expect(card).toContainText('Stopped. Nothing was answered.')
  await expect(card.locator('[data-answer-value]')).toHaveCount(0)
  await expect(planBoxes(page)).toHaveCount(0)
})

test('the 60 s turn deadline arrives as an error event and shows an error card', async ({ page }) => {
  await fakeChat(page, [
    STREAM[0],
    STREAM[1],
    { event: 'error', data: { error: 'The answer took longer than 60 s and was stopped.', code: 'timeout' } },
    { event: 'done', data: {} },
  ])
  await fakeApi(page)
  await open(page)
  await expect(page.getByRole('status').filter({ hasText: 'Live API' })).toBeVisible()
  await send(page, Q.windows)
  const card = lastCard(page)
  await expect(card.getByText('Couldn’t answer this one')).toBeVisible()
  await expect(card).toContainText('longer than 60 s')
  await expect(card.locator('[data-answer-value]')).toHaveCount(0)
  // Never answered locally behind the user's back.
  await expect(page.getByRole('status').filter({ hasText: 'Live API' })).toBeVisible()
})

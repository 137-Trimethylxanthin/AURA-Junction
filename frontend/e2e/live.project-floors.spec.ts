import { readFileSync, existsSync } from 'node:fs'
import { expect, test, type APIRequestContext } from '@playwright/test'

/**
 * Live stack (scripts/dev.sh): two image uploads of one house asked across "All 2 floors".
 * Regression for "Couldn't answer this one: Unknown project 'proj-…'": the project exists only in this browser
 * (never saved on the server), so the first ask 404s and the app must re-save it and answer.
 *
 * Inputs: AURA_E2E_PLANS="/path/b1.jpg,/path/b2.jpeg" (default ../test-data), or reuse finished uploads with
 * AURA_E2E_UPLOADS="up-a:token-a,up-b:token-b". Skips when neither is available.
 */
const API = process.env.AURA_E2E_API ?? 'http://127.0.0.1:8000'
const PLANS = (process.env.AURA_E2E_PLANS ?? '/root/test-data/b1.jpg,/root/test-data/b2.jpeg').split(',')

async function upload(request: APIRequestContext, path: string): Promise<[string, string]> {
  const r = await request.post(`${API}/uploads`, {
    headers: { origin: 'http://localhost:5173' },
    multipart: { file: { name: path.split('/').pop()!, mimeType: 'image/jpeg', buffer: readFileSync(path) } },
  })
  expect(r.status()).toBe(202)
  const body = await r.json()
  return [body.drawing_id, body.delete_token]
}

async function indexed(request: APIRequestContext, id: string) {
  await expect
    .poll(async () => (await (await request.get(`${API}/drawings/${id}/status`)).json()).stage, {
      timeout: 10 * 60_000,
      intervals: [5_000],
    })
    .toMatch(/indexed|ready_unindexed|failed/)
}

test('All 2 floors answers a room-type count over a browser-only project', async ({ page, request }) => {
  test.setTimeout(15 * 60_000)
  let ups: [string, string][]
  if (process.env.AURA_E2E_UPLOADS) {
    ups = process.env.AURA_E2E_UPLOADS.split(',').map((p) => p.split(':') as [string, string])
  } else {
    test.skip(!PLANS.every((p) => existsSync(p)), 'no plan images available')
    ups = [await upload(request, PLANS[0]), await upload(request, PLANS[1])]
  }
  for (const [id] of ups) await indexed(request, id)

  const projectId = `proj-e2e${Date.now().toString(36)}`
  await page.addInitScript(
    ([ups, projectId]) => {
      localStorage.setItem(
        'aura.uploads',
        JSON.stringify(ups.map(([id, delete_token], i) => ({ id, delete_token, name: `plan-${i}.jpg` }))),
      )
      localStorage.setItem(
        'aura.projects',
        JSON.stringify([
          {
            id: projectId,
            title: 'E2E house',
            floors: ups.map(([drawing_id], order) => ({ drawing_id, label: order ? 'Upper' : 'Lower', order })),
            auto_matched: false,
          },
        ]),
      )
    },
    [ups, projectId] as const,
  )
  await page.goto(`/d/${encodeURIComponent(ups[0][0])}`)
  const toggle = page.locator('[data-cross-plan-toggle]')
  await expect(toggle).toBeVisible({ timeout: 60_000 })
  if ((await toggle.getAttribute('aria-pressed')) !== 'true') await toggle.click()
  await expect(toggle).toHaveAttribute('aria-pressed', 'true')

  const box = page.getByRole('textbox', { name: 'Ask about this drawing' })
  await box.fill('How many bedrooms are there?')
  await box.press('Enter')
  const card = page.locator('article[data-turn]').last()
  await expect(card.locator('[aria-busy="true"]')).toHaveCount(0, { timeout: 90_000 })
  await expect(card).not.toContainText(/Unknown project/i)
  await expect(card.locator('[data-answer-value]')).toHaveAttribute('data-answer-value', '7')

  await box.fill('convert 12 m² to sq ft')
  await box.press('Enter')
  const conv = page.locator('article[data-turn]').last()
  await expect(conv.locator('[data-answer-value]')).toHaveAttribute('data-answer-value', /129[.,]17/, { timeout: 60_000 })
})

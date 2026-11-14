import { expect, test, type Page, type Route } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/**
 * WP6, mandatory: the merged upload flow on the running local stack (frontend :5173, backend :8000 in full mode).
 * Run with `npm run e2e:live` (headless). Never starts or restarts a server; retries while the backend restarts.
 *
 * 1. Fresh bytes of a Swiss Dwellings PDF (a unique PDF comment appended, so nothing is deduplicated or cached)
 *    are dropped on /upload; the pipeline must show >= 4 stages, offer "Open now" before the end and end indexed.
 * 2. In a new conversation: a door count, a labelled-room count, an apartment area and one SQL question.
 *    Answers from the answer cache fail the run; values are compared with questions.jsonl (the DXF/PDF twin).
 * 3. The upload is deleted through the page's Delete action.
 * A second test covers quick mode with /health and the status mocked.
 */

const REPO = path.resolve(__dirname, '../..')
const DATASET = path.join(REPO, 'data/junction-dataset')
const STEM = process.env.AURA_E2E_STEM ?? 'site1004_bldg2086_floor9400' // not one of the 17 bundled drawings
const API = process.env.AURA_E2E_API_BASE ?? 'http://localhost:8000'
const TABLE = '/tmp/claude-1000/-home-maki-Projects-junction-project/28275861-3944-4e09-a80f-567dee08a16e/scratchpad/e2e-upload-table.md'
const INDEX_TIMEOUT_MS = 5 * 60_000
const ANSWER_TIMEOUT_MS = 150_000
/** Door/window estimates: the error budget measured for this source by worker-backend; unknown yet → "unjudged". */
const ESTIMATE_TOLERANCE: number | null = process.env.AURA_E2E_ESTIMATE_TOL ? Number(process.env.AURA_E2E_ESTIMATE_TOL) : null

interface Truth {
  question: string
  answer: number
  unit: string
  type: string
}

function truthRows(): Truth[] {
  const lines = fs.readFileSync(path.join(DATASET, 'questions.jsonl'), 'utf8').split('\n')
  return lines
    .filter((l) => l.includes(`swiss_dwellings/${STEM}.`))
    .map((l) => JSON.parse(l) as Truth & { path: string })
    .filter((r) => /^[\x20-\x7e’'«»²]+$/.test(r.question) && /^(How|What)/.test(r.question)) // English rows
}

const findTruth = (rows: Truth[], prefix: string) => {
  const r = rows.find((x) => x.question.startsWith(prefix))
  if (!r) throw new Error(`no truth row starting with "${prefix}" for ${STEM}`)
  return r
}

/** A temp copy of the PDF with a unique trailing comment: new bytes, new hash, same drawing. */
function freshPdf(): string {
  const src = path.join(DATASET, 'data/swiss_dwellings', `${STEM}.pdf`)
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'aura-e2e-'))
  const out = path.join(dir, `${STEM}-e2e-${Date.now()}.pdf`)
  fs.writeFileSync(out, Buffer.concat([fs.readFileSync(src), Buffer.from(`\n%aura-e2e ${randomUUID()}\n`)]))
  return out
}

async function backendUp(page: Page): Promise<{ store?: string } | null> {
  for (let i = 0; i < 20; i++) {
    try {
      const r = await page.request.get(`${API}/health`, { timeout: 5000 })
      if (r.ok()) return (await r.json()) as { store?: string }
    } catch {
      /* restarting */
    }
    await page.waitForTimeout(3000)
  }
  return null
}

/** Drops a file on the drop zone the way a browser does (DataTransfer + drop event). */
async function dropFile(page: Page, file: string) {
  const b64 = fs.readFileSync(file).toString('base64')
  const name = path.basename(file)
  await page.evaluate(
    ({ b64, name }) => {
      const bin = atob(b64)
      const bytes = new Uint8Array(bin.length)
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
      const dt = new DataTransfer()
      dt.items.add(new File([bytes], name, { type: 'application/pdf' }))
      const zone = document.querySelector('[data-dropzone]')!
      zone.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true }))
      zone.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }))
    },
    { b64, name },
  )
}

interface Answered {
  question: string
  route?: string
  calls?: number
  method?: string
  value?: number
  kind?: string
  estimate?: boolean
  seconds: number
}

/** Records the `answer` event of every /chat stream (buffered: the test reads the final answer, not the stream). */
function recordAnswers(page: Page) {
  const seen: Record<string, unknown>[] = []
  void page.route('**/chat', async (route: Route) => {
    const res = await route.fetch({ timeout: ANSWER_TIMEOUT_MS })
    const body = await res.text()
    for (const block of body.split('\n\n')) {
      const ev = /^event: (.+)$/m.exec(block)?.[1]
      const data = /^data: (.+)$/m.exec(block)?.[1]
      if (ev === 'answer' && data) seen.push(JSON.parse(data) as Record<string, unknown>)
    }
    await route.fulfill({ response: res, body })
  })
  return seen
}

async function askLive(page: Page, seen: Record<string, unknown>[], question: string): Promise<Answered> {
  const box = page.getByRole('textbox', { name: 'Ask about this drawing' })
  const before = seen.length
  const cards = await page.locator('article[data-turn]').count()
  const t0 = Date.now()
  await box.fill(question)
  await box.press('Enter')
  await expect(page.locator('article[data-turn]')).toHaveCount(cards + 1)
  await expect.poll(() => seen.length, { timeout: ANSWER_TIMEOUT_MS }).toBeGreaterThan(before)
  await expect(page.locator('article[data-turn]').last().locator('[aria-busy="true"]')).toHaveCount(0, { timeout: 30_000 })
  const a = seen.at(-1)! as {
    route?: string
    calls?: number
    method?: string
    answer?: { kind?: string; value?: unknown; number?: unknown }
    estimate?: unknown
  }
  const raw = a.answer?.kind === 'note' ? a.answer.number : a.answer?.value
  return {
    question,
    route: a.route,
    calls: a.calls,
    method: a.method,
    kind: a.answer?.kind,
    value: typeof raw === 'number' ? raw : undefined,
    estimate: a.method === 'agent_estimate' || a.method === 'vision' || a.estimate !== undefined,
    seconds: Math.round((Date.now() - t0) / 100) / 10,
  }
}

test.describe('merged upload flow (live stack)', () => {
  test('fresh Swiss PDF: upload, stages, indexed, four answers vs truth, delete', async ({ page }) => {
    const health = await backendUp(page)
    test.skip(!health, `backend ${API} not reachable`)
    expect(health!.store, 'the backend must run in full mode (store on)').toBe('on')

    const truth = truthRows()
    const qDoors = findTruth(truth, 'How many doors')
    const qZimmer = findTruth(truth, "How many rooms labelled 'Zimmer'")
    const qApt = findTruth(truth, 'What is the net floor area of apartment A')
    const qSql = 'How many text labels are printed on this drawing? Use the drawing store.'

    const file = freshPdf()
    const name = path.basename(file)
    const table: string[] = []
    let drawingId = "(not uploaded)"

    try {
      // ---- 1. upload and watch the stages ------------------------------------------------------------------
      await page.goto('/upload')
      await expect(page.getByRole('heading', { name: 'Upload drawings' })).toBeVisible()
      await expect(page.locator('[data-mode="full"]')).toBeVisible()
      await dropFile(page, file)

      const card = page.locator('[data-outcome]').filter({ has: page.getByRole('heading', { name }) })
      await expect(card).toBeVisible({ timeout: 30_000 })
      const stages = new Set<string>()
      let openNowSeen = false
      let final = ''
      const deadline = Date.now() + INDEX_TIMEOUT_MS
      while (Date.now() < deadline) {
        const stage = await card.locator('[data-stage]').first().getAttribute('data-stage').catch(() => null)
        const outcome = (await card.getAttribute('data-outcome').catch(() => null)) ?? ''
        if (stage) stages.add(stage)
        if (await card.getByRole('link', { name: 'Open now' }).isVisible().catch(() => false)) openNowSeen = true
        if (outcome && outcome !== 'working') {
          final = `${stage}/${outcome}`
          break
        }
        await page.waitForTimeout(1000)
      }
      const note = await card.locator('p').allTextContents().catch(() => [])
      test.info().annotations.push({ type: 'stages', description: `${[...stages].join(' → ')} ⇒ ${final || 'timeout'}` })
      expect(stages.size, `distinct stages seen: ${[...stages].join(', ')}; notes: ${note.join(' | ')}`).toBeGreaterThanOrEqual(4)
      expect(final, `ended at ${final || 'timeout'}; stages ${[...stages].join(' → ')}`).toBe('indexed/done')
      expect(openNowSeen, '"Open now" appears while the store is still reading').toBe(true)

      const href = await card.getByRole('link', { name: 'Open', exact: true }).getAttribute('href')
      drawingId = decodeURIComponent(href!.replace(/^\/d\//, ''))

      // ---- 2. ask in a new conversation --------------------------------------------------------------------
      const seen = recordAnswers(page)
      await page.goto(`/d/${encodeURIComponent(drawingId)}`)
      const box = page.getByRole('textbox', { name: 'Ask about this drawing' })
      await expect(box).toBeVisible({ timeout: 60_000 })
      if ((await page.locator('article[data-turn]').count()) > 0) await page.getByRole('button', { name: 'New conversation' }).first().click()

      const rows: { a: Answered; truth?: number; judge: 'match' | 'area1' | 'estimate' | 'none' }[] = []
      rows.push({ a: await askLive(page, seen, qDoors.question), truth: qDoors.answer, judge: 'estimate' })
      const doorCard = page.locator('article[data-turn]').last()
      if (rows[0].a.estimate) await expect(doorCard.locator('[data-chip="method"]')).toContainText(/estimate/i)
      rows.push({ a: await askLive(page, seen, qZimmer.question), truth: qZimmer.answer, judge: 'match' })
      rows.push({ a: await askLive(page, seen, qApt.question), truth: qApt.answer, judge: 'area1' })
      rows.push({ a: await askLive(page, seen, qSql), judge: 'none' })

      // The SQL question: its query must be visible in the reasoning tree.
      const sqlCard = page.locator('article[data-turn]').last()
      await sqlCard.getByRole('button', { name: 'Reasoning tree' }).click()
      const sqlBlock = page.locator('[data-trace-panel] [data-sql]').first()
      await expect(sqlBlock, 'a SQL block in the reasoning tree').toBeVisible({ timeout: 15_000 })
      await sqlBlock.locator('summary').click()
      await expect(sqlBlock.locator('pre')).toContainText(/select/i)
      await page.getByRole('button', { name: 'Back to chat (Escape)' }).click()

      // ---- 3. judge, table ----------------------------------------------------------------------------------
      table.push('| question | answer | truth | method | route | calls | seconds | result |', '|---|---|---|---|---|---|---|---|')
      const failures: string[] = []
      for (const { a, truth: t, judge } of rows) {
        let result: string
        if (a.route === 'cache') result = 'FAIL (answer cache)'
        else if (judge === 'none') result = 'n/a (no truth)'
        else if (a.value === undefined || t === undefined) result = 'FAIL (no number)'
        else if (judge === 'match') result = a.value === t ? 'pass' : 'FAIL'
        else if (judge === 'area1') result = Math.abs(a.value - t) <= 0.01 * t ? 'pass' : 'FAIL'
        else {
          const err = Math.abs(a.value - t) / t
          result = ESTIMATE_TOLERANCE === null ? `unjudged (error ${(err * 100).toFixed(0)} %)` : err <= ESTIMATE_TOLERANCE ? 'pass' : 'FAIL'
        }
        if (result.startsWith('FAIL')) failures.push(`${a.question}: ${result}`)
        table.push(`| ${a.question.slice(0, 70)} | ${a.value ?? a.kind ?? '-'} | ${t ?? '-'} | ${a.method ?? '-'} | ${a.route ?? '-'} | ${a.calls ?? '-'} | ${a.seconds} | ${result} |`)
      }
      fs.writeFileSync(TABLE, `# WP6 upload e2e (${new Date().toISOString()}, ${STEM}, drawing ${drawingId})\n\n${table.join('\n')}\n`)
      expect(failures, `see ${TABLE}`).toEqual([])
    } finally {
      if (table.length === 0)
        fs.writeFileSync(TABLE, `# WP6 upload e2e (${new Date().toISOString()}, ${STEM}, drawing ${drawingId})\n\nStopped before the answers; see the test output.\n`)
      // ---- 4. delete through the page ---------------------------------------------------------------------
      await page.unrouteAll({ behavior: 'ignoreErrors' })
      await page.goto('/upload')
      const mine = page.locator('[data-outcome]').filter({ has: page.getByRole('heading', { name }) })
      if (await mine.count()) {
        await mine.getByRole('button', { name: `Delete ${name}` }).click().catch(async () => {
          await mine.locator('button[aria-label^="Delete"]').click()
        })
        await page.getByRole('alertdialog').getByRole('button', { name: /^Delete/ }).click()
        await expect(page.locator('[data-outcome]').filter({ has: page.getByRole('heading', { name }) })).toHaveCount(0)
      }
    }
  })
})

test.describe('quick mode (mocked /health and status)', () => {
  test('store off: ready, not indexed, estimates labelled', async ({ page }) => {
    const id = 'up-Qe2e'
    await page.route('**/health', (r) => r.fulfill({ json: { ok: true, contract_version: '1.2', store: 'off' } }))
    await page.route('**/projects**', (r) => r.fulfill({ json: { contract_version: '1.2', projects: [] } }))
    await page.route(`**/drawings/${id}/status`, (r) =>
      r.fulfill({
        json: {
          contract_version: '1.2',
          id,
          status: 'ready',
          stage: 'ready_unindexed',
          mode: 'quick',
          note: 'Quick mode: no drawing store, so rooms are not indexed for this PDF',
          counts: { pages: 1, doors: 14, windows: 9 },
          methods: { doors: 'vision', windows: 'vision' },
          elapsed_s: 2,
          stage_s: 0,
        },
      }),
    )
    await page.addInitScript(
      (u) => localStorage.setItem('aura.uploads', JSON.stringify(u)),
      [{ id, name: 'quick-plan.pdf', delete_token: 't', created_at: new Date().toISOString() }],
    )
    await page.goto('/upload')
    await expect(page.locator('[data-mode="quick"]')).toContainText('Quick mode')
    const card = page.locator('[data-outcome="quick"]')
    await expect(card).toBeVisible()
    await expect(card.locator('[data-callout="quick"]')).toContainText('not indexed')
    await expect(card.locator('[data-upload-counts]')).toContainText('Doors (estimate)')
    await expect(card.locator('[data-upload-counts]')).toContainText('Windows (estimate)')
    await expect(card.locator('[data-step][data-state="skipped"]')).toHaveCount(3)
    await expect(card.getByRole('link', { name: 'Open', exact: true })).toBeVisible()
  })
})

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, type Locator, type Page, type TestInfo } from '@playwright/test'

export interface DrawingJson {
  rooms: {
    room_id: string
    num: string
    name: string
    area_m2: number
    outdoor: boolean
    label_at: [number, number]
    rect?: { x: number; y: number; w: number; h: number; w_m: number; h_m: number }
  }[]
  evidence: Record<'window' | 'door' | 'washbasin', { id: string; x: number; y: number; w: number; h: number }[]>
}

/** The bundled drawing, read from disk so expected values are computed, not hard-coded. */
export const drawing: DrawingJson = JSON.parse(
  readFileSync(resolve(import.meta.dirname, '../src/data/drawing.swiss-46639.json'), 'utf8'),
)

export const Q = {
  windows: 'How many windows are drawn on this floor plan?',
  doors: 'How many doors are on this floor plan?',
  areaFr: 'Quelle est la surface nette de l’appartement A en m² ?',
  doorsFi: 'Montako ovea tässä pohjapiirroksessa on?',
  windowsDe: 'Wie viele Fenster?',
  fire: 'What is the fire rating of the entrance door?',
  wc: 'How many WCs are there on this floor plan?',
  basinsHere: 'Combien de lavabos ici ?',
}

export const stageOf = (page: Page) => page.locator('[data-plan-stage]')
export const composerOf = (page: Page) => page.getByRole('textbox', { name: 'Ask about this drawing' })
export const lastCard = (page: Page) => page.locator('article[data-turn]').last()
/** Boxes drawn on the plan for the active answer. */
export const planBoxes = (page: Page) => stageOf(page).locator('button[data-evidence-id]')
export const planRooms = (page: Page) => stageOf(page).locator('polygon[data-evidence-id]')

export async function open(page: Page) {
  await page.goto('/')
  await expect(composerOf(page)).toBeVisible()
  // The plan image must be in before geometry-dependent interactions.
  await expect(stageOf(page).locator('img').first()).toHaveJSProperty('complete', true)
  // The panel layout settles after first paint (ResizeObserver refits the plan): wait for a stable stage box.
  let last = ''
  await expect
    .poll(async () => {
      const now = JSON.stringify(await stageOf(page).boundingBox())
      const stable = now === last
      last = now
      return stable
    }, { intervals: [100, 100, 100, 100], timeout: 5000 })
    .toBe(true)
}

/** Type a question, send it, and wait until the newest card has left the pending state. */
export async function ask(page: Page, text: string) {
  const before = await page.locator('article[data-turn]').count()
  const box = composerOf(page)
  await box.fill(text)
  await box.press('Enter')
  await expect(page.locator('article[data-turn]')).toHaveCount(before + 1)
  await expect(lastCard(page).locator('[aria-busy="true"]')).toHaveCount(0)
}

export async function answerValue(page: Page): Promise<string | null> {
  return lastCard(page).locator('[data-answer-value]').getAttribute('data-answer-value')
}

export const goldChip = (page: Page) => lastCard(page).locator('[data-chip="gold"]')

/** Pixel position inside the stage for a point given in % of the drawing. */
export async function stagePoint(stage: Locator, xPct: number, yPct: number) {
  const b = await stage.boundingBox()
  if (!b) throw new Error('stage not laid out')
  return { x: b.x + (b.width * xPct) / 100, y: b.y + (b.height * yPct) / 100, box: b }
}

export async function shot(page: Page, name: string, info: TestInfo) {
  await page.screenshot({
    path: resolve(import.meta.dirname, '__screens__', `${name}-${info.project.name}.png`),
  })
}

/** Collect console errors and uncaught exceptions. */
export function watchConsole(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`console: ${m.text()}`)
  })
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
  return errors
}

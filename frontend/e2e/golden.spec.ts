import { expect, test } from '@playwright/test'
import {
  Q,
  answerValue,
  ask,
  composerOf,
  goldChip,
  lastCard,
  open,
  planBoxes,
  shot,
  stageOf,
} from './helpers'

test.beforeEach(async ({ page }) => {
  await open(page)
})

test.describe('windows (EN)', () => {
  test('answers 12 windows with 12 boxes on the plan and a matching dataset chip', async ({ page }, info) => {
    await ask(page, Q.windows)
    expect(await answerValue(page)).toBe('12')
    await expect(planBoxes(page)).toHaveCount(12)
    await expect(goldChip(page)).toHaveText('Dataset: 12 ✓')
    await shot(page, 'golden-windows', info)
  })

  test('rejecting box 3 by tapping it twice gives 11 and a warning chip', async ({ page }, info) => {
    await ask(page, Q.windows)
    const box3 = planBoxes(page).nth(2)
    await box3.click()
    expect(await answerValue(page)).toBe('12') // first tap only selects
    await box3.click()
    await expect(lastCard(page).locator('[data-answer-value]')).toHaveAttribute('data-answer-value', '11')
    await expect(goldChip(page)).toHaveAttribute('data-tone', 'warning')
    await shot(page, 'golden-rejected', info)
  })

  test('Undo returns the count to 12', async ({ page }) => {
    await ask(page, Q.windows)
    await planBoxes(page).nth(2).click()
    await planBoxes(page).nth(2).click()
    await expect(lastCard(page).locator('[data-answer-value]')).toHaveAttribute('data-answer-value', '11')
    await lastCard(page).getByRole('button', { name: 'Undo' }).click()
    await expect(lastCard(page).locator('[data-answer-value]')).toHaveAttribute('data-answer-value', '12')
    await expect(goldChip(page)).toHaveText('Dataset: 12 ✓')
  })

  test('Ctrl+Z while the composer has focus does not undo', async ({ page }) => {
    await ask(page, Q.windows)
    await planBoxes(page).nth(2).click()
    await planBoxes(page).nth(2).click()
    await expect(lastCard(page).locator('[data-answer-value]')).toHaveAttribute('data-answer-value', '11')
    await composerOf(page).focus()
    await page.keyboard.press('Control+z')
    // Give a wrongly-wired handler time to act before asserting nothing changed.
    await page.waitForTimeout(250)
    expect(await answerValue(page)).toBe('11')
  })

  test('Ctrl+Z outside the composer does undo', async ({ page }) => {
    await ask(page, Q.windows)
    await planBoxes(page).nth(2).click()
    await planBoxes(page).nth(2).click()
    await expect(lastCard(page).locator('[data-answer-value]')).toHaveAttribute('data-answer-value', '11')
    await composerOf(page).blur()
    await page.keyboard.press('Control+z')
    await expect(lastCard(page).locator('[data-answer-value]')).toHaveAttribute('data-answer-value', '12')
  })
})

test.describe('apartment area (FR)', () => {
  test('answers 139,34 in French with lit polygons', async ({ page }, info) => {
    await ask(page, Q.areaFr)
    expect(await answerValue(page)).toBe('139,34')
    const card = lastCard(page)
    await expect(card).toContainText('Surface nette')
    await expect(card).toContainText('Pièces incluses')
    await expect(goldChip(page)).toContainText('Jeu de données')
    const lit = stageOf(page).locator('polygon[data-evidence-id][aria-pressed="true"]')
    expect(await lit.count()).toBeGreaterThan(0)
    await shot(page, 'golden-area-fr', info)
  })

  test('adding terrace 3.01 gives 169,73', async ({ page }) => {
    await ask(page, Q.areaFr)
    await lastCard(page)
      .getByRole('button', { name: /^Add Terrasse.*3\.01$/ })
      .click()
    await expect(lastCard(page).locator('[data-answer-value]')).toHaveAttribute('data-answer-value', '169,73')
  })
})

test('Finnish door question gives 22 with Finnish copy', async ({ page }) => {
  await ask(page, Q.doorsFi)
  expect(await answerValue(page)).toBe('22')
  await expect(lastCard(page)).toContainText('ovea')
  await expect(goldChip(page)).toContainText('Aineisto')
})

test('German question "Wie viele Fenster?" gives 12 with German copy', async ({ page }) => {
  await ask(page, Q.windowsDe)
  expect(await answerValue(page)).toBe('12')
  await expect(lastCard(page)).toContainText('Fenster')
  await expect(goldChip(page)).toContainText('Datensatz')
})

test('fire rating question abstains without any digits in the answer card head', async ({ page }, info) => {
  await ask(page, Q.fire)
  const card = lastCard(page)
  await expect(card.locator('[data-abstain="not_on_drawing"]')).toBeVisible()
  await expect(card.locator('[data-answer-value]')).toHaveCount(0)
  const head = await card.locator('[data-abstain-title], [data-abstain-body]').allTextContents()
  expect(head.join(' ')).not.toMatch(/\d/)
  await expect(planBoxes(page)).toHaveCount(0)
  await shot(page, 'golden-abstain', info)
})

test('WC question says the fixtures are not extracted yet', async ({ page }) => {
  await ask(page, Q.wc)
  const card = lastCard(page)
  await expect(card.locator('[data-abstain="not_supported_yet"]')).toBeVisible()
  await expect(card).toContainText('aren’t extracted yet')
  await expect(card.locator('[data-answer-value]')).toHaveCount(0)
})

test('composer stays inside the viewport without scrolling after 6 turns', async ({ page }, info) => {
  const questions = [Q.windows, Q.areaFr, Q.doorsFi, Q.windowsDe, Q.fire, Q.doors]
  for (const q of questions) await ask(page, q)
  const vp = page.viewportSize()!
  const box = await composerOf(page).boundingBox()
  expect(box).not.toBeNull()
  expect(box!.y).toBeGreaterThanOrEqual(0)
  expect(box!.y + box!.height).toBeLessThanOrEqual(vp.height)
  await expect(composerOf(page)).toBeInViewport({ ratio: 1 })
  const scrolls = await page.evaluate(() => ({
    doc: document.documentElement.scrollHeight - window.innerHeight,
    y: window.scrollY,
  }))
  expect(scrolls.doc).toBeLessThanOrEqual(0)
  expect(scrolls.y).toBe(0)
  await shot(page, 'golden-six-turns', info)
})

test('row numbers in the evidence list match the badges on the plan for the same evidence id', async ({ page }) => {
  await ask(page, Q.doors)
  const rows = lastCard(page).locator('li[data-evidence-id]')
  const n = await rows.count()
  expect(n).toBe(22)
  for (let i = 0; i < n; i++) {
    const id = await rows.nth(i).getAttribute('data-evidence-id')
    const rowBadge = (await rows.nth(i).locator('.num').first().textContent())?.trim()
    const planBadge = (await stageOf(page).locator(`[data-badge-for="${id}"]`).textContent())?.trim()
    expect(planBadge, `badge of ${id}`).toBe(rowBadge)
  }
})

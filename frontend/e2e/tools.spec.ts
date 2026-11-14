import { expect, test, type Page } from '@playwright/test'
import { Q, answerValue, ask, drawing, lastCard, open, planBoxes, shot, stageOf, stagePoint } from './helpers'

const room = (id: string) => drawing.rooms.find((r) => r.room_id === id)!
const menu = (page: Page) => page.getByRole('menu', { name: 'Plan actions' })

/** Centre of a room's rectangle in % of the drawing (rooms without a rect use their label point). */
function centreOf(id: string): [number, number] {
  const r = room(id)
  return r.rect ? [r.rect.x + r.rect.w / 2, r.rect.y + r.rect.h / 2] : r.label_at
}

async function rightClickRoom(page: Page, id: string) {
  const [x, y] = centreOf(id)
  const p = await stagePoint(stageOf(page), x, y)
  await page.mouse.click(p.x, p.y, { button: 'right' })
}

/**
 * A real touch press at a page point, via CDP so Chrome emits genuine touch pointer events. It is held for at least
 * `ms`, then `whileHeld` runs (still pressed) before release, so a busy page cannot see the finger lift before its
 * long-press timer has had a chance to fire.
 */
async function longPress(page: Page, x: number, y: number, ms: number, whileHeld?: () => Promise<void>) {
  const cdp = await page.context().newCDPSession(page)
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] })
  try {
    await page.waitForTimeout(ms)
    await whileHeld?.()
  } finally {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
    await cdp.detach()
  }
}

test.beforeEach(async ({ page }) => {
  await open(page)
})

test.describe('context menu', () => {
  test('right-click on room 3.04 opens a menu titled "Chambre à coucher 3.04"', async ({ page }, info) => {
    await rightClickRoom(page, '3.04')
    await expect(menu(page)).toBeVisible()
    await expect(menu(page).locator('[data-slot="context-menu-label"]')).toContainText('Chambre à coucher 3.04')
    await shot(page, 'tools-context-menu', info)
  })

  test('the menu has three groups', async ({ page }) => {
    await rightClickRoom(page, '3.04')
    await expect(menu(page).locator('[data-slot="context-menu-group"]')).toHaveCount(3)
  })

  test('the menu shows no keyboard shortcuts', async ({ page }) => {
    await rightClickRoom(page, '3.04')
    await expect(menu(page)).toBeVisible()
    await expect(menu(page).locator('[data-slot="context-menu-shortcut"], kbd')).toHaveCount(0)
  })

  test('terrace 3.13 has no "Change sizes" item', async ({ page }) => {
    await rightClickRoom(page, '3.13')
    await expect(menu(page).locator('[data-slot="context-menu-label"]')).toContainText('3.13')
    await expect(menu(page).getByRole('menuitem', { name: /Change sizes/ })).toHaveCount(0)
  })

  test('room 3.04 offers "Change sizes of 3.04" (control for the terrace case)', async ({ page }) => {
    await rightClickRoom(page, '3.04')
    await expect(menu(page).getByRole('menuitem', { name: 'Change sizes of 3.04' })).toBeVisible()
  })

  test('a 900 ms touch long-press on room 3.04 opens the same menu', async ({ page }, info) => {
    test.skip(!info.project.use.hasTouch, 'touch projects only')
    const [x, y] = centreOf('3.04')
    const p = await stagePoint(stageOf(page), x, y)
    // Everything is asserted while the finger is still down.
    await longPress(page, p.x, p.y, 900, async () => {
      await expect(menu(page)).toBeVisible({ timeout: 5000 })
      await expect(menu(page).locator('[data-slot="context-menu-label"]')).toContainText('Chambre à coucher 3.04')
      await expect(menu(page).locator('[data-slot="context-menu-group"]')).toHaveCount(3)
      await expect(menu(page).locator('[data-slot="context-menu-shortcut"], kbd')).toHaveCount(0)
    })
  })

  test('the long-press menu stays open after the finger lifts', async ({ page }, info) => {
    test.skip(!info.project.use.hasTouch, 'touch projects only')
    const [x, y] = centreOf('3.04')
    const p = await stagePoint(stageOf(page), x, y)
    await longPress(page, p.x, p.y, 900, () => expect(menu(page)).toBeVisible({ timeout: 5000 }))
    await page.waitForTimeout(400)
    await expect(menu(page)).toBeVisible()
  })

  test('a long-press while Mark is active does not open the menu', async ({ page }, info) => {
    test.skip(!info.project.use.hasTouch, 'touch projects only')
    await page.locator('[aria-label="Mark area"]').click()
    const [x, y] = centreOf('3.04')
    const p = await stagePoint(stageOf(page), x, y)
    await longPress(page, p.x, p.y, 1000)
    await page.waitForTimeout(300)
    await expect(menu(page)).toHaveCount(0)
  })
})

test('marking the bathroom block and asking counts the washbasins whose centre is inside the mark', async ({ page }, info) => {
  await page.locator('[aria-label="Mark area"]').click()
  // 3.06 and 3.09 in % of the drawing; basin-03's centre sits just below the bottom edge.
  const a = await stagePoint(stageOf(page), 47, 38)
  const b = await stagePoint(stageOf(page), 61, 51)
  await page.mouse.move(a.x, a.y)
  await page.mouse.down()
  await page.mouse.move((a.x + b.x) / 2, (a.y + b.y) / 2, { steps: 4 })
  await page.mouse.move(b.x, b.y, { steps: 4 })
  await page.mouse.up()

  const mark = stageOf(page).locator('[data-mark-id]')
  await expect(mark).toHaveCount(1)
  // Read the mark the app stored (its rect is what scopes the question), not the numbers we dragged.
  const rect = await mark.evaluate((el) => {
    const s = (el as HTMLElement).style
    return { x: parseFloat(s.left), y: parseFloat(s.top), w: parseFloat(s.width), h: parseFloat(s.height) }
  })
  const expected = drawing.evidence.washbasin.filter((bx) => {
    const cx = bx.x + bx.w / 2
    const cy = bx.y + bx.h / 2
    return cx >= rect.x && cx <= rect.x + rect.w && cy >= rect.y && cy <= rect.y + rect.h
  }).length
  expect(expected).toBeGreaterThan(0)
  expect(expected).toBeLessThan(drawing.evidence.washbasin.length)

  await expect(page.getByText('Area 1 attached')).toBeVisible()
  await ask(page, Q.basinsHere)
  expect(await answerValue(page)).toBe(String(expected))
  await expect(planBoxes(page)).toHaveCount(expected)
  // The scope is a region: the card says the count is "in Area 1", not on the whole plan.
  await expect(lastCard(page)).toContainText('Area 1')
  await shot(page, 'tools-mark', info)
})

test.describe('compare', () => {
  async function openCompare(page: Page) {
    await page.locator('[aria-label="Compare with original"]').click()
    // The compare layer is code-split: wait until its divider is on screen before dragging it.
    await expect(page.getByRole('slider', { name: /^Compare:/ })).toBeVisible()
  }
  const divider = (page: Page) => page.getByRole('slider', { name: /^Compare:/ })

  test('the swipe divider follows the pointer', async ({ page }, info) => {
    await openCompare(page)
    const stage = stageOf(page)
    const from = await stagePoint(stage, 50, 50)
    const to = await stagePoint(stage, 30, 50)
    await page.mouse.move(from.x, from.y)
    await page.mouse.down()
    await page.mouse.move(to.x, to.y, { steps: 6 })
    await page.mouse.up()
    const v = Number(await divider(page).getAttribute('aria-valuenow'))
    expect(v).toBeGreaterThanOrEqual(29)
    expect(v).toBeLessThanOrEqual(31)
    await shot(page, 'tools-swipe', info)
  })

  test('the swipe divider clamps to 2 at the left and 98 at the right', async ({ page }) => {
    await openCompare(page)
    const stage = stageOf(page)
    const mid = await stagePoint(stage, 50, 50)
    await page.mouse.move(mid.x, mid.y)
    await page.mouse.down()
    await page.mouse.move(mid.box.x - 150, mid.y, { steps: 6 })
    expect(Number(await divider(page).getAttribute('aria-valuenow'))).toBe(2)
    await page.mouse.move(mid.box.x + mid.box.width + 150, mid.y, { steps: 10 })
    expect(Number(await divider(page).getAttribute('aria-valuenow'))).toBe(98)
    await page.mouse.up()
  })

  test('the blend slider changes the opacity of the original', async ({ page }) => {
    await openCompare(page)
    await page.getByRole('radio', { name: 'Blend' }).click()
    const original = stageOf(page).locator('img[alt^="Original drawing"]')
    const slider = page.locator('[data-slot="slider-thumb"]')
    await slider.focus()
    await page.keyboard.press('Home')
    await expect.poll(() => original.evaluate((el) => getComputedStyle(el).opacity)).toBe('0')
    await page.keyboard.press('End')
    await expect.poll(() => original.evaluate((el) => getComputedStyle(el).opacity)).toBe('1')
  })
})

test('what-if depth 3.60 on room 3.04 gives 137,19 in French, labelled as the what-if size', async ({ page }, info) => {
  await ask(page, Q.areaFr) // sets the session language to French
  await rightClickRoom(page, '3.04')
  await menu(page).getByRole('menuitem', { name: 'Change sizes of 3.04' }).click()
  const editor = page.getByRole('region', { name: 'What-if sizes' })
  await expect(editor).toBeVisible()
  await expect(editor.getByLabel('Wall length (m)')).toHaveValue('4.135')
  await editor.getByLabel('Room depth (m)').fill('3.60')
  await editor.getByRole('button', { name: 'Ask the agent with these sizes' }).click()
  await expect(page.locator('article[data-turn]')).toHaveCount(2)
  await expect(lastCard(page).locator('[aria-busy="true"]')).toHaveCount(0)
  expect(await answerValue(page)).toBe('137,19')
  await expect(lastCard(page).locator('li[data-evidence-id="room-3.04"]')).toContainText('vos dimensions hypothétiques')
  await shot(page, 'tools-whatif', info)
})

test.describe('reduced motion', () => {
  test.use({ reducedMotion: 'reduce' })

  test('selecting a box leaves no running transform animation', async ({ page }) => {
    await ask(page, Q.windows)
    await planBoxes(page).nth(2).click()
    await page.waitForTimeout(120)
    const running = await page.evaluate(() =>
      document
        .getAnimations()
        .filter((a) => a.playState === 'running')
        .filter((a) => {
          const kf = (a.effect as KeyframeEffect | null)?.getKeyframes?.() ?? []
          return kf.some((k) => ['transform', 'translate', 'scale', 'rotate'].some((p) => p in k))
        })
        .map((a) => `${(a as CSSAnimation).animationName ?? a.constructor.name} on ${(a.effect as KeyframeEffect).target?.className}`),
    )
    expect(running).toEqual([])
  })
})

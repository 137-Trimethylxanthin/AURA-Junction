import { expect, test } from '@playwright/test'
import { composerOf, lastCard, open } from './helpers'

test('the interface language is chosen in the header, persists, and never changes the answer language', async ({ page }) => {
  await open(page)
  await page.locator('[data-lang-menu]').click()
  await page.getByRole('menuitemradio', { name: 'Deutsch' }).click()
  await expect(page.getByRole('toolbar', { name: 'Planwerkzeuge' })).toBeVisible()
  await expect(page.locator('html')).toHaveAttribute('lang', 'de')
  await page.reload()
  await expect(composerOf(page)).toHaveCount(0)
  const box = page.getByRole('textbox', { name: 'Frage zu diesem Plan' })
  await expect(box).toBeVisible()
  // An English question still gets an English answer card.
  await box.fill('How many windows are drawn on this floor plan?')
  await box.press('Enter')
  await expect(lastCard(page).locator('[data-answer-value]')).toHaveAttribute('data-answer-value', '12')
  await expect(lastCard(page)).toContainText('windows')
  await page.evaluate(() => localStorage.removeItem('aura.ui-lang'))
})

for (const lang of ['de', 'fi', 'fr'] as const)
  test(`${lang}: the toolbar and header fit at 1366×768 with the sidebar dragged wide`, async ({ page }, info) => {
    test.skip(info.project.name !== 'desktop', 'laptop layout')
    await page.setViewportSize({ width: 1366, height: 768 })
    await page.addInitScript((l) => localStorage.setItem('aura.ui-lang', l), lang)
    await page.goto('/d/swiss-46639')
    await expect(page.locator('[data-plan-stage] img').first()).toBeVisible()
    const handle = page.locator('[data-slot="resizable-handle"]').first()
    const b = (await handle.boundingBox())!
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2)
    await page.mouse.down()
    await page.mouse.move(b.x - 600, b.y + b.height / 2, { steps: 8 })
    await page.mouse.up()
    for (const sel of ['[data-slot="toolbar"]', 'header']) {
      const o = await page.locator(sel).first().evaluate((el) => ({ s: el.scrollWidth, c: el.clientWidth }))
      expect(o.s, sel).toBeLessThanOrEqual(o.c + 1)
    }
    await expect(page.locator('[data-lang-menu]')).toContainText(lang)
    if (process.env.AURA_SHOTS) await page.screenshot({ path: `${process.env.AURA_SHOTS}/toolbar-${lang}-1366.png` })
  })

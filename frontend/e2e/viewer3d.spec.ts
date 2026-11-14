import { expect, test } from '@playwright/test'
import { Q, ask, open, shot } from './helpers'

test.beforeEach(async ({ page }) => {
  await open(page)
})

const viewer = (page: import('@playwright/test').Page) => page.getByTestId('viewer3d')

test('3D view builds the bundled plan and switches back to 2D', async ({ page }, info) => {
  await page.getByRole('radio', { name: '3D' }).click()
  await expect(viewer(page)).toHaveAttribute('data-v3d-state', 'ready', { timeout: 20_000 })
  await expect(viewer(page).locator('canvas')).toBeVisible()
  // One label per room of the drawing (24 on swiss-46639).
  await expect(viewer(page).locator('.v3d-label')).toHaveCount(24)
  await shot(page, 'viewer3d-plan', info)
  await page.getByRole('radio', { name: '2D' }).click()
  await expect(viewer(page)).toHaveCount(0)
})

test('answer evidence becomes numbered pins in 3D', async ({ page }) => {
  await page.getByRole('radio', { name: '3D' }).click()
  await expect(viewer(page)).toHaveAttribute('data-v3d-state', 'ready', { timeout: 20_000 })
  await ask(page, Q.windows)
  await expect(viewer(page).locator('.v3d-pin')).toHaveCount(12)
})

test('a dropped-in image is shown locally and the drawing comes back', async ({ page }) => {
  // Two scene builds in software WebGL: under a fully parallel run this can pass the default 30 s.
  test.slow()
  await page.getByRole('radio', { name: '3D' }).click()
  await expect(viewer(page)).toHaveAttribute('data-v3d-state', 'ready', { timeout: 20_000 })
  await page.locator('[data-testid="viewer3d"] input[type=file]').setInputFiles('public/og.png')
  await expect(viewer(page).getByRole('button', { name: 'Back to the drawing' })).toBeVisible()
  await expect(viewer(page)).toHaveAttribute('data-v3d-state', 'ready', { timeout: 20_000 })
  await expect(viewer(page).locator('.v3d-label')).toHaveCount(0)
  await viewer(page).getByRole('button', { name: 'Back to the drawing' }).click()
  await expect(viewer(page).locator('.v3d-label')).toHaveCount(24, { timeout: 20_000 })
})

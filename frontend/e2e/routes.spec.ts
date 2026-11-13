import { expect, test } from '@playwright/test'
import { composerOf, smallTargets, smallText, watchConsole } from './helpers'

/**
 * Routes of the local build (no VITE_API_URL): landing, library, workspace, redirects, 404 and the root error
 * boundary. The library with a (faked) API lives in api.library.spec.ts.
 */

const SAMPLE = '/d/swiss-46639'

test('the library lists the bundled sample, honestly labelled, and opens it', async ({ page }) => {
  const errors = watchConsole(page)
  await page.goto('/app')
  await expect(page).toHaveTitle('Drawings · AURA')
  await expect(page.getByRole('heading', { level: 1, name: 'Drawings' })).toBeVisible()
  await expect(page.locator('[data-offline-note]')).toBeVisible()
  await expect(page.locator('[data-uploads-off]')).toBeVisible()
  const card = page.locator('[data-drawing-card="swiss-46639"]')
  await expect(card).toContainText('Bundled sample')
  await expect(card).toContainText('CC BY 4.0')
  // No measured facts come with the local build, so none are shown.
  await expect(page.locator('[data-facts], [data-full-set]')).toHaveCount(0)
  expect(await smallText(page)).toEqual([])
  expect(await smallTargets(page)).toEqual([])
  await card.getByRole('link').click()
  await expect(page).toHaveURL(SAMPLE)
  await expect(composerOf(page)).toBeVisible()
  await expect(page).toHaveTitle('Plan 3e étage · AURA')
  expect(errors).toEqual([])
})

test('keyboard: the sample card is reachable with Tab and opens with Enter', async ({ page }) => {
  await page.goto('/app')
  const link = page.locator('[data-drawing-card="swiss-46639"] a')
  for (let i = 0; i < 12 && !(await link.evaluate((el) => el === document.activeElement)); i++) await page.keyboard.press('Tab')
  await expect(link).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(SAMPLE)
})

test('the workspace wordmark leads back to the library', async ({ page }) => {
  await page.goto(SAMPLE)
  await page.getByRole('link', { name: 'AURA: all drawings' }).click()
  await expect(page).toHaveURL('/app')
})

test('the landing page renders at /', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('[data-not-found], [data-route-error]')).toHaveCount(0)
  await expect(page.locator('h1').first()).toBeVisible()
})

for (const path of ['/credits', '/privacy']) {
  test(`${path} renders`, async ({ page }) => {
    await page.goto(path)
    await expect(page.locator('[data-not-found], [data-route-error]')).toHaveCount(0)
    await expect(page.locator('h1').first()).toBeVisible()
  })
}

test('unknown paths show the 404 page with a way back', async ({ page }) => {
  await page.goto('/no/such/page')
  await expect(page.locator('[data-not-found]')).toBeVisible()
  await expect(page).toHaveTitle('Page not found · AURA')
  await page.getByRole('link', { name: 'Open the sample drawing' }).click()
  await expect(page).toHaveURL(SAMPLE)
})

test('short links redirect: /demo opens the sample, /d opens the library', async ({ page }) => {
  await page.goto('/demo')
  await expect(page).toHaveURL(SAMPLE)
  await expect(composerOf(page)).toBeVisible()
  await page.goto('/d')
  await expect(page).toHaveURL('/app')
})

test('another drawing id without a server explains why it cannot open', async ({ page }) => {
  await page.goto('/d/upload-123')
  await expect(page.locator('[data-drawing-load="unavailable"]')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'This drawing needs the AURA server' })).toBeVisible()
  await page.getByRole('link', { name: 'Open the sample drawing' }).click()
  await expect(composerOf(page)).toBeVisible()
})

test('a page whose code fails to download shows the root error boundary', async ({ page }) => {
  await page.route(/\/assets\/LibraryPage-[^/]+\.js$/, (r) => r.abort())
  await page.goto('/app')
  await expect(page.locator('[data-route-error]')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'This page did not finish loading' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Reload' })).toBeVisible()
})

test('a ?presenter= link stores the token and is removed from the address bar', async ({ page }) => {
  await page.goto('/app?presenter=tok-123&x=1')
  await expect(page).toHaveURL('/app?x=1')
  expect(await page.evaluate(() => localStorage.getItem('aura.presenter'))).toBe('tok-123')
})

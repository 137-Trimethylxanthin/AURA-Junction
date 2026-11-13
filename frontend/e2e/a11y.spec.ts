import { expect, test, type Page } from '@playwright/test'
import { Q, ask, lastCard, open, planBoxes, watchConsole } from './helpers'

/** Min hit-box edge in CSS px. */
const MIN = 40

interface Finding {
  what: string
  w: number
  h: number
}

/**
 * Hit-area check. An element passes when its own box is at least MIN x MIN. Otherwise we measure its real hit area
 * (the box plus any ::before hit-area pseudo-element) by hit-testing: from the element's centre we step outwards
 * 1 px at a time along each axis with elementFromPoint, up to MIN px each way, and stop at the first point that is
 * neither the element, one of its descendants, its own number badge, nor another interactive target (a neighbour's
 * hit area also counts as "tappable", so dense boxes are not reported twice). The measured extent on each axis
 * (left + right, up + down) must be at least MIN - 1 px (sub-pixel rounding). This deliberately does not assume the
 * hit area is centred, so an off-centre pseudo-element is measured by its real size.
 */
async function undersizedTargets(page: Page): Promise<Finding[]> {
  return page.evaluate((MIN) => {
    const out: { what: string; w: number; h: number }[] = []
    const sel = 'button, [role=menuitem], [data-evidence-id]'
    const describe = (el: Element) =>
      `${el.tagName.toLowerCase()}${el.getAttribute('aria-label') ? `[${el.getAttribute('aria-label')!.slice(0, 50)}]` : ''}${el.textContent ? ` "${el.textContent.trim().slice(0, 24)}"` : ''}`
    for (const el of Array.from(document.querySelectorAll(sel))) {
      if (!(el as HTMLElement).checkVisibility?.({ checkVisibilityCSS: true } as never)) continue
      if (el.closest('[inert], [aria-hidden="true"]')) continue
      const r = el.getBoundingClientRect()
      if (r.width === 0 || r.height === 0) continue
      if (r.right < 0 || r.bottom < 0 || r.left > innerWidth || r.top > innerHeight) continue
      if (r.width >= MIN && r.height >= MIN) continue
      const id = el.getAttribute('data-evidence-id')
      const cx = r.left + r.width / 2
      const cy = r.top + r.height / 2
      const tappable = (x: number, y: number) => {
        if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return true
        const hit = document.elementFromPoint(x, y)
        if (!hit) return false
        if (el.contains(hit) || hit.contains(el)) return hit !== document.body && !hit.matches('[data-layer], [data-plan-stage]') ? true : el.contains(hit)
        if (id && hit.closest(`[data-badge-for="${id}"]`)) return true
        return !!hit.closest(sel + ', [data-badge-for]')
      }
      const reach = (dx: number, dy: number) => {
        let n = 0
        while (n < MIN && tappable(cx + dx * (n + 1), cy + dy * (n + 1))) n++
        return n
      }
      const w = reach(-1, 0) + reach(1, 0)
      const h = reach(0, -1) + reach(0, 1)
      if (w < MIN - 1 || h < MIN - 1) out.push({ what: describe(el), w, h })
    }
    return out
  }, MIN)
}

test.beforeEach(async ({ page }) => {
  await open(page)
})

test('every visible button, menu item and evidence target has a 40 x 40 px hit area (empty state)', async ({ page }) => {
  expect(await undersizedTargets(page)).toEqual([])
})

test('every visible button, menu item and evidence target has a 40 x 40 px hit area (windows answer)', async ({ page }) => {
  await ask(page, Q.windows)
  await planBoxes(page).nth(2).click()
  expect(await undersizedTargets(page)).toEqual([])
})

test('every visible button, menu item and evidence target has a 40 x 40 px hit area (apartment area answer)', async ({ page }) => {
  await ask(page, Q.areaFr)
  expect(await undersizedTargets(page)).toEqual([])
})

test('plan context-menu items have a 40 px hit height', async ({ page }) => {
  const stage = page.locator('[data-plan-stage]')
  const b = (await stage.boundingBox())!
  await page.mouse.click(b.x + b.width * 0.55, b.y + b.height * 0.3, { button: 'right' })
  await expect(page.getByRole('menu', { name: 'Plan actions' })).toBeVisible()
  expect(await undersizedTargets(page)).toEqual([])
})

test('no visible text is rendered below 12 px', async ({ page }) => {
  await ask(page, Q.windows)
  await ask(page, Q.areaFr)
  const small = await page.evaluate(() => {
    const out = new Set<string>()
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const text = n.textContent?.trim()
      const el = n.parentElement
      if (!text || !el) continue
      if (!el.checkVisibility?.({ checkVisibilityCSS: true } as never)) continue
      const r = el.getBoundingClientRect()
      if (r.width < 2 || r.height < 2) continue // sr-only
      const size = parseFloat(getComputedStyle(el).fontSize)
      if (size < 12) out.add(`${size}px "${text.slice(0, 30)}" in <${el.tagName.toLowerCase()} class="${el.className}">`)
    }
    return [...out]
  })
  expect(small).toEqual([])
})

test('no console errors or page errors across the golden flow', async ({ page }) => {
  const errors = watchConsole(page)
  await ask(page, Q.windows)
  await planBoxes(page).nth(2).click()
  await planBoxes(page).nth(2).click()
  await lastCard(page).getByRole('button', { name: 'Undo' }).click()
  await ask(page, Q.areaFr)
  await lastCard(page)
    .getByRole('button', { name: /^Add Terrasse.*3\.01$/ })
    .click()
  await ask(page, Q.doorsFi)
  await ask(page, Q.windowsDe)
  await ask(page, Q.fire)
  await ask(page, Q.wc)
  await lastCard(page).getByRole('button', { name: 'Reasoning tree' }).click()
  await page.waitForTimeout(300)
  expect(errors).toEqual([])
})

test('every slider has an accessible name', async ({ page }) => {
  await page.locator('[aria-label="Compare with original"]').click()
  await page.getByRole('radio', { name: 'Blend' }).click()
  const unnamed = await page.evaluate(() =>
    Array.from(document.querySelectorAll('[role=slider]'))
      .filter((el) => {
        const lab = el.getAttribute('aria-label') ?? ''
        const by = el.getAttribute('aria-labelledby')
        const labelled = by ? !!document.getElementById(by)?.textContent?.trim() : false
        return !lab.trim() && !labelled
      })
      .map((el) => el.outerHTML.slice(0, 120)),
  )
  expect(unnamed).toEqual([])
})

test.describe('hover styles are gated to hover-capable pointers', () => {
  // A suggestion chip carries `hover:bg-muted` through the project's gated `hover` variant.
  const chip = (page: Page) => page.getByRole('button', { name: /How many windows/ }).first()
  const bg = (page: Page) => chip(page).evaluate((el) => getComputedStyle(el).backgroundColor)

  test('control: a fine, hovering pointer changes the chip background on hover', async ({ page }) => {
    const matches = await page.evaluate(() => matchMedia('(hover: hover) and (pointer: fine)').matches)
    test.skip(!matches, 'this project does not emulate a hovering pointer')
    const before = await bg(page)
    await chip(page).hover()
    await expect.poll(() => bg(page)).not.toBe(before)
  })

  test.describe('coarse pointer', () => {
    test.use({ isMobile: true, hasTouch: true, viewport: { width: 820, height: 1180 } })

    test('hovering a chip does not change its background', async ({ page }) => {
      expect(await page.evaluate(() => matchMedia('(hover: none)').matches)).toBe(true)
      const before = await bg(page)
      await chip(page).hover()
      await page.waitForTimeout(300)
      expect(await bg(page)).toBe(before)
    })
  })
})

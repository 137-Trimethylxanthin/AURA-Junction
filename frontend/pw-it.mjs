import { chromium } from 'playwright'
import { globSync } from 'node:fs'
const exe = globSync('/root/.cache/ms-playwright/chromium-*/chrome-linux64/chrome')[0]
const out = '/root/aura-work/verify/r/'
const b = await chromium.launch({ executablePath: exe, args: ['--no-sandbox'] })
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } })
await ctx.addInitScript(() => localStorage.setItem('aura.ui-lang', 'it'))
const p = await ctx.newPage()
const errs = []
p.on('pageerror', (e) => errs.push(String(e).slice(0, 200)))
const base = 'http://127.0.0.1:5295'
async function ask(q, wait = 25000) {
  const ta = p.locator('textarea').first()
  await ta.fill(q)
  await ta.press('Enter')
  await p.waitForTimeout(wait)
}
const steps = process.argv.slice(2)
const run = async (name, fn) => { if (!steps.length || steps.includes(name)) { try { await fn() } catch (e) { console.log(name, 'ERR', String(e).slice(0, 300)) } } }
await run('lib', async () => {
  await p.goto(base + '/app'); await p.waitForTimeout(6000)
  await p.screenshot({ path: out + 'it-library.png' })
})
await run('swiss', async () => {
  await p.goto(base + '/d/swiss-2233'); await p.waitForTimeout(7000)
  await p.screenshot({ path: out + 'it-swiss-open.png' })
  await ask('Quante porte ci sono?', 15000)
  await p.screenshot({ path: out + 'it-swiss-porte.png' })
  await ask('Qual è la superficie netta in piedi quadrati?', 15000)
  await p.screenshot({ path: out + 'it-swiss-sqft.png' })
})
await run('scan', async () => {
  await p.goto(base + '/d/st-colorful-11397-60115c71'); await p.waitForTimeout(8000)
  await ask('Qual è il locale più grande?', 20000)
  await p.screenshot({ path: out + 'it-scan-largest.png' })
})
await run('ifc', async () => {
  await p.goto(base + '/d/ifc-hitos_arc--1-etg'); await p.waitForTimeout(9000)
  await p.screenshot({ path: out + 'it-ifc-open.png' })
  const all = p.getByText(/Tutti i \d+ piani/).first()
  if (await all.count()) { await all.click(); await p.waitForTimeout(1500) } else console.log('no all-floors control')
  await ask('Quante porte ci sono?', 25000)
  await p.screenshot({ path: out + 'it-ifc-all-floors.png' })
})
console.log('errors', errs)
console.log((await p.locator('body').innerText()).slice(0, 3000))
await b.close()

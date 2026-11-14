import { defineConfig, devices } from '@playwright/test'

/**
 * Runs only api.spec.ts and api.library.spec.ts against a build that has VITE_API_URL baked in (the app reads it at build time).
 *   npx playwright test -c e2e/playwright.api.config.ts
 * The API base is on the preview's own origin (/__api), so there is no CORS; page.route fakes every call.
 */
process.env.AURA_E2E_API = '1'
const PORT = 4174
const OUT = 'node_modules/.aura-e2e-api'

export default defineConfig({
  testDir: '.',
  testMatch: ['api.spec.ts', 'api.library.spec.ts', 'api.chat.spec.ts', 'api.rewrite.spec.ts', 'api.overflow.spec.ts'],
  fullyParallel: true,
  reporter: 'list',
  outputDir: '../test-results/api-run',
  use: { baseURL: `http://localhost:${PORT}`, trace: 'retain-on-failure' },
  webServer: {
    command:
      `VITE_API_URL=http://localhost:${PORT}/__api npx vite build --outDir ${OUT} --emptyOutDir && ` +
      `npx vite preview --outDir ${OUT} --port ${PORT} --strictPort`,
    cwd: '..',
    url: `http://localhost:${PORT}`,
    reuseExistingServer: true,
    timeout: 180_000,
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'tablet-landscape', use: { ...devices['Desktop Chrome'], viewport: { width: 1024, height: 768 }, hasTouch: true } },
    { name: 'tablet-portrait', use: { ...devices['Desktop Chrome'], viewport: { width: 820, height: 1180 }, hasTouch: true } },
  ],
})

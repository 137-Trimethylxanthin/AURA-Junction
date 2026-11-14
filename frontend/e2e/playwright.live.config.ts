import { defineConfig, devices } from '@playwright/test'

/**
 * WP6: the merged upload flow against the RUNNING local stack (scripts/dev.sh): frontend :5173, backend :8000.
 *   npm run e2e:live
 * Headless only; it never starts, stops or restarts a server.
 */
export default defineConfig({
  testDir: '.',
  testMatch: ['upload-merged.spec.ts'],
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  outputDir: '../test-results/live-run',
  timeout: 12 * 60_000,
  use: {
    baseURL: process.env.AURA_E2E_BASE ?? 'http://localhost:5173',
    headless: true,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, headless: true } }],
})

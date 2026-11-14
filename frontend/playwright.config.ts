import { defineConfig, devices } from "@playwright/test"

export default defineConfig({
  testDir: "e2e",
  // Needs the API build (e2e/playwright.api.config.ts); not a skip here, so the expected-skip count stays meaningful.
  testIgnore: ["api.library.spec.ts", "api.chat.spec.ts", "api.rewrite.spec.ts", "upload-merged.spec.ts", "live.*.spec.ts"],
  fullyParallel: true,
  reporter: "list",
  use: { baseURL: "http://localhost:4173", trace: "retain-on-failure" },
  webServer: {
    // The local suite tests the build without a backend: an empty VITE_API_URL overrides a developer's .env.local.
    command: "VITE_API_URL= npm run build && npm run preview",
    url: "http://localhost:4173",
    reuseExistingServer: true,
    timeout: 120_000,
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    {
      name: "tablet-landscape",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1024, height: 768 }, hasTouch: true },
    },
    {
      name: "tablet-portrait",
      use: { ...devices["Desktop Chrome"], viewport: { width: 820, height: 1180 }, hasTouch: true },
    },
  ],
})

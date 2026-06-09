import { defineConfig, devices } from "@playwright/test";

// LaborAid E2E suite (Tier 3 rework loop + earlier flows).
//
// Target: the deployed CloudFront SPA (override via E2E_BASE_URL when pointing
// at a preview/staging URL). Auth runs once in tests/e2e/global.setup.ts and
// stores Amplify-compatible localStorage state at storageStateFile so every
// other spec inherits a logged-in session.
//
// Local run:
//   E2E_PASSWORD='LaborAid2026Demo!' pnpm test:e2e
// Or interactive:
//   E2E_PASSWORD=... pnpm test:e2e:headed
const BASE_URL = process.env.E2E_BASE_URL ?? "https://d3ggwschjt81wu.cloudfront.net";

export default defineConfig({
  testDir: "./tests/e2e",
  // Tier 3 flow takes 30-40s end-to-end (network + AWS calls).
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "playwright-report" }],
  ],
  outputDir: "test-results",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    viewport: { width: 1440, height: 900 },
  },
  // Global setup primes the demo state (resets 704/2026-01-01 → v1
  // pending_review) and writes the auth storage state.
  globalSetup: "./tests/e2e/global.setup.ts",
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // Reuse the storage state written by global.setup so every test is
        // already authenticated as demo@laboraid.test.
        storageState: "tests/e2e/.auth/user.json",
      },
    },
  ],
});

import { defineConfig, devices } from '@playwright/test'

/**
 * Browser checks run against an isolated Compose project started by tests/e2e/run.sh
 * (default http://localhost:3180, Mailpit API on :8085). Live state is shared by the whole
 * programme, so tests run serially.
 *
 * Projects: Chromium with a Pixel 7 profile, WebKit with an iPhone 14 profile (WebKit engine
 * emulation, not a real-device Safari check) and desktop Chromium.
 */
const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost:3180'

export default defineConfig({
  testDir: './specs',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    { name: 'mobile-chromium', use: { ...devices['Pixel 7'] } },
    { name: 'mobile-webkit', use: { ...devices['iPhone 14'] } },
    { name: 'desktop-chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
  ],
})

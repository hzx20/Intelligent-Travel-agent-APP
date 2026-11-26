import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  testMatch: 'draft-guard.spec.js',
  outputDir: './playwright-results/drafts',
  workers: 1,
  retries: 0,
  timeout: 30000,
  forbidOnly: true,
  reporter: 'list',
  use: {
    browserName: 'chromium',
    channel: process.env.TRAVEL_E2E_CHANNEL || 'msedge',
    baseURL: 'http://127.0.0.1:5263/travel/',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1280, height: 900 } } },
    { name: 'mobile', use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
  webServer: {
    command: 'node node_modules/vite/bin/vite.js --config qa/draft-guard/vite.config.mjs',
    url: 'http://127.0.0.1:5263/travel/',
    reuseExistingServer: false,
    timeout: 30000,
  },
})

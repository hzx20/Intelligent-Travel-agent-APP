import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './e2e', testMatch: 'travel-suppliers.spec.js', workers: 1, retries: 0,
  timeout: 30000, reporter: 'list', outputDir: './playwright-results/suppliers',
  use: { browserName: 'chromium', channel: process.env.TRAVEL_E2E_CHANNEL || 'msedge',
    baseURL: 'http://127.0.0.1:5287/travel/', screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1280, height: 900 } } },
    { name: 'mobile', use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
  webServer: { command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5287 --strictPort --base /travel/',
    url: 'http://127.0.0.1:5287/travel/', reuseExistingServer: false, timeout: 30000 },
})

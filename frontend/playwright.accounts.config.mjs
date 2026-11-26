import { defineConfig } from '@playwright/test'
import drafts from './playwright.config.mjs'

export default defineConfig({
  ...drafts,
  testMatch: 'account-sharing.spec.js',
  timeout: 60000,
  globalSetup: './e2e/account-backend.setup.js',
  outputDir: './playwright-results/accounts',
  use: { ...drafts.use, baseURL: 'http://127.0.0.1:5264/travel/' },
  webServer: {
    command: 'node node_modules/vite/bin/vite.js --config qa/account-sharing/vite.config.mjs',
    url: 'http://127.0.0.1:5264/travel/',
    reuseExistingServer: false,
    timeout: 30000,
  },
})

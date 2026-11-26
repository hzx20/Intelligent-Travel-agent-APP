import { defineConfig } from '@playwright/test'
import accounts from './playwright.accounts.config.mjs'

export default defineConfig({
  ...accounts,
  testMatch: 'trip-creation.spec.js',
  outputDir: './playwright-results/creation',
})

import {defineConfig} from '@playwright/test'
import accounts from './playwright.accounts.config.mjs'
export default defineConfig({...accounts,testMatch:'share-recovery.spec.js',outputDir:'./playwright-results/sharing'})

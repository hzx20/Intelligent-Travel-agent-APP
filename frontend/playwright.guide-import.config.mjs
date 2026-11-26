import {defineConfig} from '@playwright/test'
import accounts from './playwright.accounts.config.mjs'
export default defineConfig({...accounts,metadata:{guideImportFixture:true},testMatch:'guide-import.spec.js',outputDir:'./playwright-results/guide-import'})

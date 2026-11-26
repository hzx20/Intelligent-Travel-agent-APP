import {defineConfig} from '@playwright/test'
import accounts from './playwright.accounts.config.mjs'
export default defineConfig({...accounts,metadata:{planFixture:true},testMatch:'plan-recovery.spec.js',outputDir:'./playwright-results/plan-recovery'})

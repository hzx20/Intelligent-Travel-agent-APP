import {defineConfig} from '@playwright/test'
import recovery from './playwright.plan-recovery.config.mjs'
export default defineConfig({...recovery,testMatch:'plan-history.spec.js',outputDir:'./playwright-results/plan-history'})

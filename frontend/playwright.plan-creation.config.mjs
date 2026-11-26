import {defineConfig} from '@playwright/test'
import recovery from './playwright.plan-recovery.config.mjs'
export default defineConfig({...recovery,testMatch:'plan-creation.spec.js',outputDir:'./playwright-results/plan-creation'})

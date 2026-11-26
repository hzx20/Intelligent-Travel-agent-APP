import {defineConfig} from '@playwright/test'
import recovery from './playwright.plan-recovery.config.mjs'
export default defineConfig({...recovery,testMatch:'trip-home.spec.js',outputDir:'./playwright-results/trip-home'})

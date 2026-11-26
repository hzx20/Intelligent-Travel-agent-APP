import {defineConfig} from '@playwright/test'
import accounts from './playwright.accounts.config.mjs'
export default defineConfig({...accounts,metadata:{seedSpots:true},testMatch:'spot-collection.spec.js',outputDir:'./playwright-results/spot-collection'})

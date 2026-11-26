import {defineConfig} from '@playwright/test'
import accounts from './playwright.accounts.config.mjs'
export default defineConfig({...accounts,metadata:{discoveryFixture:true},testMatch:'discovery.spec.js',outputDir:'./playwright-results/discovery'})

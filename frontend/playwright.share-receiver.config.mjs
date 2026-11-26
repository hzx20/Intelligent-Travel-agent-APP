import {defineConfig} from '@playwright/test'
import accounts from './playwright.accounts.config.mjs'
export default defineConfig({...accounts,testMatch:'share-receiver.spec.js',outputDir:'./playwright-results/share-receiver'})

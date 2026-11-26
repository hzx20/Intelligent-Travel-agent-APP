import {defineConfig} from '@playwright/test'
import accounts from './playwright.accounts.config.mjs'
export default defineConfig({...accounts,testMatch:'guide-editor.spec.js',outputDir:'./playwright-results/guide-editor'})

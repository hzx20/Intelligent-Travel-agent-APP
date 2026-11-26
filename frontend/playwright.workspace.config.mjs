import {defineConfig} from '@playwright/test'
import accounts from './playwright.accounts.config.mjs'
export default defineConfig({...accounts,metadata:{workspaceFixture:true},testMatch:'workspace.spec.js',outputDir:'./playwright-results/workspace'})

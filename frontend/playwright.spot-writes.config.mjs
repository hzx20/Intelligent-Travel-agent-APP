import {defineConfig} from '@playwright/test'
import collection from './playwright.spot-collection.config.mjs'
export default defineConfig({...collection,testMatch:'spot-writes.spec.js',outputDir:'./playwright-results/spot-writes'})

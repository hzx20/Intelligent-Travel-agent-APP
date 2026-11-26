import accounts from './playwright.accounts.config.mjs'
export default { ...accounts, testMatch: 'map-loading.spec.js', outputDir: './playwright-results/map-loading' }

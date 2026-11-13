import { defineConfig } from '@playwright/test'
import config from './playwright.config'

export default defineConfig({
  ...config,
  testMatch: '**/library-sync.production.spec.ts',
  testIgnore: [],
  outputDir: './live-results/production',
  workers: 1,
  use: {
    ...config.use,
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://frontend:5174',
  },
})

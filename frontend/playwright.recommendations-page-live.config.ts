import { defineConfig } from '@playwright/test'
import config from './playwright.recommendations-live.config'

export default defineConfig({
  ...config,
  testMatch: '**/recommendations-page.live.spec.ts',
  outputDir: './live-results/recommendations-page',
})

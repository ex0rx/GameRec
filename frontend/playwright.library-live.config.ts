import { defineConfig } from '@playwright/test'
import config from './playwright.recommendations-live.config'

export default defineConfig({
  ...config,
  testMatch: '**/library.live.spec.ts',
  outputDir: './live-results/library',
})

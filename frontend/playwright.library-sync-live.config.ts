import { defineConfig } from '@playwright/test'
import config from './playwright.recommendations-live.config'

export default defineConfig({
  ...config,
  testMatch: '**/library-sync.live.spec.ts',
  outputDir: './live-results/library-sync',
  timeout: 240_000,
})

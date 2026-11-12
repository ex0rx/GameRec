import { defineConfig } from '@playwright/test'
import config from './playwright.config'

export default defineConfig({
  ...config,
  testMatch: '**/recommendations.live.spec.ts',
  testIgnore: [],
  workers: 1,
  timeout: 120_000,
  outputDir: './live-results/recommendations',
  use: { ...config.use, trace: 'off', screenshot: 'off', video: 'off' },
})

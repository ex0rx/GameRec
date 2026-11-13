import { defineConfig } from '@playwright/test'
import config from './playwright.config'

export default defineConfig({
  ...config,
  testMatch: '**/discovery.live.spec.ts',
  testIgnore: [],
  workers: 1,
  outputDir: './live-results',
  timeout: 240_000,
  use: { ...config.use, trace: 'off', screenshot: 'off', video: 'off' },
})

import { defineConfig } from '@playwright/test'
import config from './playwright.recommendations-live.config'

export default defineConfig({
  ...config,
  testMatch: '**/auth-proxy.live.spec.ts',
  outputDir: './live-results/auth/proxy-check',
})

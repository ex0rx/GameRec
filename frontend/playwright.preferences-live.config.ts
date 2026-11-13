import { defineConfig } from '@playwright/test'
import config from './playwright.recommendations-live.config'

const stage = process.env.GAMEREC_PREFERENCE_STAGE
if (!stage || !['baseline', 'liked', 'disliked', 'restored'].includes(stage))
  throw new Error('Use the privacy-safe --preferences launcher.')

export default defineConfig({
  ...config,
  testMatch: '**/preferences.live.spec.ts',
  outputDir: `./live-results/preferences/${stage}/browser`,
  timeout: 180_000,
})

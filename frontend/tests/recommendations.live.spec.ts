import { expect, test } from '@playwright/test'
import { writeFile } from 'node:fs/promises'
import type { RecommendationResponse } from '../src/api/types'

test('live recommendation client and hook through the frontend proxy', async ({
  page,
}, testInfo) => {
  const steamId = process.env.GAMEREC_TEST_STEAM_ID
  test.skip(!steamId, 'Use the local verification launcher to supply the development ID privately.')
  await page.goto('/tests/recommendations.html')
  await expect(page.getByTestId('state')).toHaveText('disabled')
  const responsePromise = page.waitForResponse('**/api/recommendations')
  await page.evaluate(async (id) => {
    const moduleUrl = '/tests/recommendations-harness.tsx'
    const { applyRecommendationTestInputs } = (await import(
      /* @vite-ignore */ moduleUrl
    )) as typeof import('./recommendations-harness')
    applyRecommendationTestInputs(id!, 20)
  }, steamId)
  const response = await responsePromise
  expect(response.status()).toBe(200)
  await expect(page.getByTestId('state')).toHaveText('success')
  const data = (await response.json()) as RecommendationResponse
  expect(data.total).toBeGreaterThan(0)
  expect(data.total).toBe(data.games.length)
  expect(data.total).toBeLessThanOrEqual(20)
  expect(
    data.games.every(
      (game, index) => index === 0 || data.games[index - 1].hybrid_score >= game.hybrid_score,
    ),
  ).toBe(true)
  const body = response.request().postDataJSON() as { steamid64: unknown; limit: number }
  expect(typeof body.steamid64).toBe('string')
  // Report counts/status only; no user ID, request body or taste data is written.
  await writeFile(
    testInfo.outputPath('verification.json'),
    JSON.stringify(
      {
        status: response.status(),
        total: data.total,
        limit: data.limit,
        steam_id_sent_as_string: typeof body.steamid64 === 'string',
        server_order_preserved: true,
      },
      null,
      2,
    ),
  )
})

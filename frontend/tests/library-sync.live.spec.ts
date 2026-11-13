import { expect, test } from '@playwright/test'
import { writeFile } from 'node:fs/promises'
import type { LibraryResponse } from '../src/api/types'

test('one explicitly authorised library sync and immediate cooldown', async ({
  page,
}, testInfo) => {
  const steamId = process.env.GAMEREC_TEST_STEAM_ID
  test.skip(!steamId, 'Use the explicit privacy-safe launcher with --library-sync.')
  const initialResponse = page.waitForResponse('**/api/users/*/library?**')
  await page.goto(`/library/${steamId}`)
  const initial = await initialResponse
  expect(initial.status()).toBe(200)
  const before = (await initial.json()) as LibraryResponse
  // Fail before any write unless the real backend explicitly permits this account.
  expect(before.sync_allowed).toBe(true)
  const syncResponse = page.waitForResponse('**/api/users/*/library/sync')
  await page.getByRole('button', { name: 'Sync Library', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Syncing…', exact: true })).toBeDisabled()
  const completed = await syncResponse
  expect(completed.status()).toBe(200)
  const result = (await completed.json()) as {
    status: string
    processed_games: number
    total_games: number
    library_last_synced_at: string
    metadata_enrichment: string
  }
  expect(result.status).toBe('completed')
  expect(['complete', 'partial']).toContain(result.metadata_enrichment)
  expect(result.processed_games).toBeGreaterThanOrEqual(0)
  await expect(page.getByText(/Library synced\./)).toBeVisible()

  const storedResponse = await page.request.get(`/api/users/${steamId}/library?limit=100&offset=0`)
  expect(storedResponse.status()).toBe(200)
  const stored = (await storedResponse.json()) as LibraryResponse
  expect(stored.total_games).toBe(result.total_games)
  expect(stored.library_last_synced_at).toBe(result.library_last_synced_at)
  expect(
    Date.parse(stored.library_last_synced_at!) >
      Date.parse(before.library_last_synced_at ?? '1970-01-01'),
  ).toBe(true)
  expect(
    stored.games.every(
      (game) =>
        game.playtime_forever_minutes === null ||
        (Number.isInteger(game.playtime_forever_minutes) && game.playtime_forever_minutes >= 0),
    ),
  ).toBe(true)

  // This second POST must be rejected by the coordinator before another Steam request.
  const cooldown = await page.request.post(`/api/users/${steamId}/library/sync`)
  expect(cooldown.status()).toBe(429)
  expect(Number(cooldown.headers()['retry-after'])).toBeGreaterThan(0)
  const rejection = (await cooldown.json()) as { detail: { code: string } }
  expect(rejection.detail.code).toBe('sync_cooldown')
  const rows = page.locator('article.library-game-row')
  await expect(rows).toHaveCount(Math.min(result.total_games, before.limit))
  await page.evaluate(() => document.fonts.ready)
  for (const [name, width, height] of [
    ['desktop', 1440, 1080],
    ['mobile', 390, 844],
  ] as const) {
    await page.setViewportSize({ width, height })
    await page.evaluate(() => window.scrollTo(0, 0))
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({
      path: testInfo.outputPath(`${name}-sync.png`),
      animations: 'disabled',
      mask: [page.locator('[data-private-steam-id]')],
      maskColor: '#303238',
    })
  }
  await writeFile(
    testInfo.outputPath('verification.json'),
    JSON.stringify(
      {
        sync_status: completed.status(),
        processed_games: result.processed_games,
        stored_games: stored.total_games,
        returned_games_checked: stored.games.length,
        persisted_timestamp_matches: true,
        timestamp_advanced: true,
        playtime_values_valid: true,
        metadata_enrichment: result.metadata_enrichment,
        immediate_cooldown_status: cooldown.status(),
        retry_after_seconds: Number(cooldown.headers()['retry-after']),
        screenshots_identity_masked: true,
      },
      null,
      2,
    ),
  )
})

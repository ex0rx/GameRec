import { expect, test } from '@playwright/test'
import { writeFile } from 'node:fs/promises'
import path from 'node:path'
import type { LibraryResponse, RecommendationResponse } from '../src/api/types'

interface SelectedGame {
  steam_app_id: number
  name: string
  page: number
}

test('temporary preference stage uses persisted data and the real recommendation hook', async ({
  page,
}) => {
  const steamId = process.env.GAMEREC_TEST_STEAM_ID
  const stage = process.env.GAMEREC_PREFERENCE_STAGE
  test.skip(!steamId || !stage, 'Use the explicit --preferences launcher.')
  const games = JSON.parse(process.env.GAMEREC_PREFERENCE_CASES || '[]') as SelectedGame[]
  expect(games).toHaveLength(2)
  const selected = stage === 'disliked' ? games[1] : games[0]
  const library = page.waitForResponse('**/api/users/*/library?**')
  await page.goto(`/library/${steamId}?page=${selected.page}`)
  const response = await library
  expect(response.status()).toBe(200)
  const body = (await response.json()) as LibraryResponse
  expect(body.preference_writes_allowed).toBe(true)
  const row = page.locator(`[data-steam-app-id="${selected.steam_app_id}"]`)
  await expect(row).toBeVisible()
  if (stage === 'liked' || stage === 'disliked') {
    const action = stage === 'liked' ? 'Like' : 'Dislike'
    const mutation = page.waitForResponse(
      (candidate) =>
        candidate.request().method() === 'PUT' &&
        new URL(candidate.url()).pathname.endsWith(`/preferences/${selected.steam_app_id}`),
    )
    await row.getByRole('button', { name: `${action} ${selected.name}`, exact: true }).click()
    expect((await mutation).status()).toBe(200)
    await expect(
      row.getByRole('button', { name: `${action} ${selected.name}`, exact: true }),
    ).toHaveAttribute('aria-pressed', 'true')
  }
  // Read both persisted Library pages; preference-list pagination cannot hide a choice.
  const saved: (string | null)[] = []
  for (const game of games) {
    const persisted = await page.request.get(
      `/api/users/${steamId}/library?limit=50&offset=${(game.page - 1) * 50}`,
    )
    expect(persisted.status()).toBe(200)
    const snapshot = (await persisted.json()) as LibraryResponse
    const stored = snapshot.games.find((entry) => entry.steam_app_id === game.steam_app_id)
    expect(stored).toBeDefined()
    saved.push(stored!.preference)
  }
  expect(saved).toEqual(
    stage === 'baseline' || stage === 'restored'
      ? [null, null]
      : stage === 'liked'
        ? ['liked', null]
        : ['liked', 'disliked'],
  )
  if (stage === 'liked' || stage === 'disliked') {
    await page.evaluate(() => document.fonts.ready)
    for (const [name, width, height] of [
      ['desktop', 1440, 1080],
      ['mobile', 390, 844],
    ] as const) {
      await page.setViewportSize({ width, height })
      await row.scrollIntoViewIfNeeded()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      )
      await page.screenshot({
        path: path.resolve(`live-results/preferences/${stage}/${name}.png`),
        animations: 'disabled',
        mask: [page.locator('[data-private-steam-id]')],
        maskColor: '#303238',
      })
    }
  }
  const recommendations = page.waitForResponse(
    (candidate) =>
      candidate.request().method() === 'POST' &&
      new URL(candidate.url()).pathname.endsWith('/recommendations'),
  )
  await page.getByRole('link', { name: 'View recommendations', exact: true }).click()
  const ranked = await recommendations
  expect(ranked.status()).toBe(200)
  const results = (await ranked.json()) as RecommendationResponse
  expect(results.limit).toBe(20)
  expect(results.games.length).toBeGreaterThan(0)
  expect(results.games.every((game) => Number.isFinite(game.hybrid_score))).toBe(true)
  expect(results.games.map((game) => game.hybrid_score)).toEqual(
    [...results.games.map((game) => game.hybrid_score)].sort((a, b) => b - a),
  )
  await writeFile(
    path.resolve(`live-results/preferences/${stage}/stage.json`),
    JSON.stringify(
      { stage, http_status: ranked.status(), total: results.total, games: results.games },
      null,
      2,
    ),
  )
})

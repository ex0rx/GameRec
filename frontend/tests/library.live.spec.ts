import { expect, test } from '@playwright/test'
import { writeFile } from 'node:fs/promises'
import type { LibraryResponse, RecommendationResponse } from '../src/api/types'

test('live stored library through the frontend proxy', async ({ page }, testInfo) => {
  const steamId = process.env.GAMEREC_TEST_STEAM_ID
  test.skip(!steamId, 'Use the privacy-safe local launcher with --library.')
  const calls: string[] = []
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    if (path.startsWith('/api/')) calls.push(path)
  })
  await page.goto('/library')
  await expect(page.getByRole('heading', { name: 'Game Library', exact: true })).toBeVisible()
  expect(calls.length).toBe(0)
  const firstResponse = page.waitForResponse('**/api/users/*/library?**')
  await page.getByLabel('Steam ID64', { exact: true }).fill(steamId!)
  await page.getByRole('button', { name: 'Load library', exact: true }).click()
  const first = await firstResponse
  expect(first.status()).toBe(200)
  const data = (await first.json()) as LibraryResponse
  expect(typeof data.steamid64 === 'string' && data.steamid64 === steamId).toBe(true)
  expect(data.total_games > 0 && data.games.length > 0 && data.games.length <= 50).toBe(true)
  const rows = page.locator('article.library-game-row')
  await expect(rows).toHaveCount(data.games.length)
  expect(
    await rows
      .locator('h3')
      .allTextContents()
      .then((titles) =>
        titles.every(
          (title, index) =>
            title ===
            (data.games[index].name.trim() || `Steam app ${data.games[index].steam_app_id}`),
        ),
      ),
  ).toBe(true)
  let paginationVerified = false
  let secondPageReturned: number | null = null
  if (data.total_games > data.limit) {
    const nextResponse = page.waitForResponse('**/api/users/*/library?**')
    await page
      .getByRole('navigation', { name: 'Library pages' })
      .getByRole('button', { name: 'Next', exact: true })
      .click()
    const next = await nextResponse
    expect(next.status()).toBe(200)
    const second = (await next.json()) as LibraryResponse
    expect(second.offset).toBe(data.limit)
    expect(second.total_games).toBe(data.total_games)
    expect(second.games.length).toBe(Math.min(second.limit, second.total_games - second.offset))
    secondPageReturned = second.games.length
    expect(
      second.games.every(
        (game) => !data.games.some((firstGame) => firstGame.steam_app_id === game.steam_app_id),
      ),
    ).toBe(true)
    await expect(rows).toHaveCount(second.games.length)
    await page
      .getByRole('navigation', { name: 'Library pages' })
      .getByRole('button', { name: 'Previous', exact: true })
      .click()
    await expect(rows).toHaveCount(data.games.length)
    paginationVerified = true
  }
  await page.evaluate(() => document.fonts.ready)
  for (const [name, width, height] of [
    ['desktop', 1440, 1080],
    ['tablet', 768, 1024],
    ['mobile', 390, 844],
  ] as const) {
    await page.setViewportSize({ width, height })
    for (const row of await rows.all().then((items) => items.slice(0, 6))) {
      await row.scrollIntoViewIfNeeded()
      await row
        .locator('img')
        .evaluateAll((images) =>
          Promise.all(images.map((image) => (image as HTMLImageElement).decode().catch(() => {}))),
        )
    }
    await page.evaluate(async () => {
      window.scrollTo(0, 0)
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    })
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true)
    await page.screenshot({
      path: testInfo.outputPath(`${name}.png`),
      animations: 'disabled',
      mask: [page.locator('[data-private-steam-id]')],
      maskColor: '#303238',
    })
    if (name !== 'tablet') {
      await page.getByRole('navigation', { name: 'Library pages' }).scrollIntoViewIfNeeded()
      for (const row of await rows.all().then((items) => items.slice(-5))) {
        await row
          .locator('img')
          .evaluateAll((images) =>
            Promise.all(
              images.map((image) => (image as HTMLImageElement).decode().catch(() => {})),
            ),
          )
      }
      await page.screenshot({
        path: testInfo.outputPath(`${name}-pagination.png`),
        animations: 'disabled',
        mask: [page.locator('[data-private-steam-id]')],
        maskColor: '#303238',
      })
    }
  }
  expect(calls.every((path) => /^\/api\/users\/[0-9]{17}\/library$/.test(path))).toBe(true)
  const libraryRequests = calls.length
  // A separate explicit user action verifies the Library-to-Recommendations journey.
  const recommendationResponse = page.waitForResponse('**/api/recommendations')
  await page.getByRole('link', { name: 'View recommendations', exact: true }).click()
  const recommendations = await recommendationResponse
  expect(recommendations.status()).toBe(200)
  const recommended = (await recommendations.json()) as RecommendationResponse
  expect(recommendations.request().postDataJSON().steamid64 === steamId).toBe(true)
  await expect(page).toHaveURL('/recommendations')
  await expect(page.getByRole('article')).toHaveCount(recommended.games.length)
  expect(calls.slice(libraryRequests)).toEqual(['/api/recommendations'])
  await writeFile(
    testInfo.outputPath('verification.json'),
    JSON.stringify(
      {
        status: first.status(),
        total_games: data.total_games,
        returned: data.games.length,
        limit: data.limit,
        server_order_preserved: true,
        pagination_verified: paginationVerified,
        second_page_returned: secondPageReturned,
        library_requests: libraryRequests,
        other_api_requests_before_handoff: 0,
        explicit_recommendation_status: recommendations.status(),
        recommendations_returned: recommended.games.length,
        screenshots_identity_masked: true,
      },
      null,
      2,
    ),
  )
})

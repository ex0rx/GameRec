import { expect, test } from '@playwright/test'
import { writeFile } from 'node:fs/promises'
import type { RecommendationResponse } from '../src/api/types'

test('live recommendations page through the frontend proxy', async ({ page }, testInfo) => {
  const steamId = process.env.GAMEREC_TEST_STEAM_ID
  test.skip(!steamId, 'Supply the development ID privately through the local launcher.')
  const apiPaths: string[] = []
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    if (path.startsWith('/api/')) apiPaths.push(path)
  })
  await page.setViewportSize({ width: 1440, height: 1080 })
  await page.goto('/recommendations')
  await expect(page.getByRole('heading', { name: 'No profile selected' })).toBeVisible()
  expect(apiPaths.length).toBe(0)
  await page.screenshot({ path: testInfo.outputPath('initial-desktop.png') })
  const responsePromise = page.waitForResponse('**/api/recommendations')
  await page.getByLabel('Development Steam ID').fill(steamId!)
  await page.getByRole('button', { name: 'Load recommendations' }).click()
  const response = await responsePromise
  expect(response.status()).toBe(200)
  const data = (await response.json()) as RecommendationResponse
  expect(data.total > 0 && data.total <= 20).toBe(true)
  await expect(page.getByRole('article')).toHaveCount(data.total)
  // Booleans keep private result data out of assertion diagnostics.
  expect(
    await page
      .locator('.game-title-row h3')
      .allTextContents()
      .then((titles) =>
        titles.every(
          (title, index) =>
            title ===
            (data.games[index].name?.trim() || `Steam app ${data.games[index].steam_app_id}`),
        ),
      ),
  ).toBe(true)
  expect(
    await page
      .getByLabel('Development Steam ID')
      .inputValue()
      .then((value) => value.length === 0),
  ).toBe(true)
  expect(new URL(page.url()).search).toBe('')
  await page.evaluate(() => document.fonts.ready)
  // Settle lazy artwork before capturing the catalogue, including failure fallbacks.
  for (const card of await page.getByRole('article').all()) {
    await card.scrollIntoViewIfNeeded()
    await card
      .locator('img')
      .evaluateAll((images) =>
        Promise.all(images.map((image) => (image as HTMLImageElement).decode().catch(() => {}))),
      )
  }
  for (const [name, width, height] of [
    ['desktop', 1440, 1080],
    ['tablet', 768, 1024],
    ['mobile', 390, 844],
  ] as const) {
    await page.setViewportSize({ width, height })
    await page.getByRole('article').first().scrollIntoViewIfNeeded()
    await page
      .getByRole('article')
      .first()
      .locator('img')
      .evaluateAll((images) =>
        Promise.all(images.map((image) => (image as HTMLImageElement).decode().catch(() => {}))),
      )
    await page.evaluate(async () => {
      window.scrollTo(0, 0)
      // Allow offscreen artwork to paint again after scrolling back to the header.
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    })
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`${name}.png`), animations: 'disabled' })
    await page.screenshot({
      path: testInfo.outputPath(`${name}-full.png`),
      fullPage: true,
      animations: 'disabled',
    })
  }
  await page.getByRole('button', { name: 'View details', exact: true }).first().click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('mobile-details.png'), animations: 'disabled' })
  await page.keyboard.press('Escape')
  expect(apiPaths).toEqual(['/api/recommendations'])
  await writeFile(
    testInfo.outputPath('verification.json'),
    JSON.stringify(
      {
        status: response.status(),
        total: data.total,
        limit: data.limit,
        server_order_preserved: true,
        explanation_requests: 0,
        identity_in_url: false,
        viewports: [1440, 768, 390],
      },
      null,
      2,
    ),
  )
})

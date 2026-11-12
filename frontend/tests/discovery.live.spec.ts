import { expect, test } from '@playwright/test'
import { writeFile } from 'node:fs/promises'

// No interception: these bounded checks use the real API, model and catalogue.
test('live search, artwork and explicit explanation through the frontend proxy', async ({
  page,
}, testInfo) => {
  const query = 'Cooperative survival crafting game with challenging bosses'
  const apiCalls: { path: string; status: number }[] = []
  let explanationCalls = 0
  page.on('response', (response) => {
    if (new URL(response.url()).pathname.startsWith('/api/'))
      apiCalls.push({ path: new URL(response.url()).pathname, status: response.status() })
  })
  page.on('request', (request) => {
    if (request.url().endsWith('/api/explanations/search')) explanationCalls++
  })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/discover')
  await page.screenshot({ path: testInfo.outputPath('desktop-start.png'), animations: 'disabled' })
  await page.getByLabel('What would you like to play?').fill(query)
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  await expect(page.getByRole('article').first()).toBeVisible({ timeout: 30_000 })
  const returned = await page.getByRole('article').count()
  await page
    .locator('.artwork img')
    .first()
    .evaluate((image) => (image as HTMLImageElement).decode())
    .catch(() => {})
  await page.screenshot({
    path: testInfo.outputPath('desktop-results.png'),
    animations: 'disabled',
  })
  expect(explanationCalls).toBe(0)
  const firstTitle = await page.locator('.game-title-row h3').first().innerText()
  await page.getByRole('article').first().getByRole('button', { name: 'Why this game?' }).click()
  await expect(page.locator('.explanation-output')).toBeVisible({ timeout: 200_000 })
  expect(explanationCalls).toBe(1)
  await page.screenshot({
    path: testInfo.outputPath('desktop-explanation.png'),
    animations: 'disabled',
  })
  const explanationText = await page.locator('.explanation-output').innerText()
  await page.keyboard.press('Escape')
  await page.setViewportSize({ width: 768, height: 1024 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('tablet-results.png'), animations: 'disabled' })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('mobile-results.png'), animations: 'disabled' })
  await page.getByRole('article').first().getByRole('button', { name: 'Why this game?' }).click()
  await expect(page.locator('.explanation-output')).toBeVisible({ timeout: 200_000 })
  await page.screenshot({
    path: testInfo.outputPath('mobile-explanation.png'),
    animations: 'disabled',
  })
  expect(apiCalls.every((response) => response.status === 200)).toBe(true)
  const evidencePath = testInfo.outputPath('live-api-evidence.json')
  await writeFile(
    evidencePath,
    JSON.stringify(
      {
        query,
        firstTitle,
        explanationText,
        returned,
        apiCalls,
        explanationCalls,
      },
      null,
      2,
    ),
  )
  await testInfo.attach('live-api-evidence', {
    path: evidencePath,
    contentType: 'application/json',
  })
})

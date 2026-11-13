import { expect, test } from '@playwright/test'
import { libraryResponse } from './library-fixtures'
import { mockCurrentUser } from './auth-fixtures'

test('production bundle exposes owner controls without automatic mutations', async ({
  page,
}) => {
  await mockCurrentUser(page, '76561190000000001')
  let mutations = 0
  page.on('request', (request) => {
    if (request.method() !== 'GET') mutations++
  })
  await page.route('**/api/users/*/library?**', (route) =>
    route.fulfill({
      json: libraryResponse(undefined, { syncAllowed: true, preferencesAllowed: true }),
    }),
  )
  await page.route('https://shared.fastly.steamstatic.com/**', (route) => route.abort())
  await page.goto('/library')
  await expect(page.locator('article.library-game-row')).toHaveCount(3)
  await expect(page.getByRole('button', { name: 'Sync Library', exact: true })).toBeVisible()
  await expect(page.getByRole('group', { name: /^Preference for / })).toHaveCount(3)
  expect(mutations).toBe(0)
})


test('production bundle hides owner controls when signed out', async ({ page }) => {
  await mockCurrentUser(page, null)
  await page.goto('/library')
  await expect(page.getByRole('heading', { name: 'Sign in to continue' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Sync Library|Syncing/ })).toHaveCount(0)
  await expect(page.getByRole('group', { name: /^Preference for / })).toHaveCount(0)
})

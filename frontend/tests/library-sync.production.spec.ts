import { expect, test } from '@playwright/test'
import { libraryResponse } from './library-fixtures'
import { USER_A } from './recommendation-fixtures'

test('production bundle hides write controls even when the backend advertises permission', async ({
  page,
}) => {
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
  await page.goto(`/library/${USER_A}`)
  await expect(page.locator('article.library-game-row')).toHaveCount(3)
  await expect(page.getByRole('button', { name: /Sync Library|Syncing/ })).toHaveCount(0)
  await expect(page.getByRole('group', { name: /^Preference for / })).toHaveCount(0)
  expect(mutations).toBe(0)
})

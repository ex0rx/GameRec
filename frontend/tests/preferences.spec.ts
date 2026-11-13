import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import type { GamePreference } from '../src/api/types'
import { libraryGames, libraryResponse } from './library-fixtures'
import { game as recommendation, USER_A, USER_B } from './recommendation-fixtures'

const libraryRoute = '**/api/users/*/library?**'
const preferenceRoute = '**/api/users/*/preferences/*'
const row = (page: Page, appId = 40) => page.locator(`[data-steam-app-id="${appId}"]`)
const like = (page: Page, appId = 40) => row(page, appId).getByRole('button', { name: /^Like / })
const dislike = (page: Page, appId = 40) =>
  row(page, appId).getByRole('button', { name: /^Dislike / })

async function selectLibrary(page: Page, id: string) {
  await page.getByLabel('Steam ID64', { exact: true }).fill(id)
  await page.getByRole('button', { name: 'Load library', exact: true }).click()
}

function preferenceResponse(id: string, appId: number, preference: GamePreference) {
  return {
    steamid64: id,
    steam_app_id: appId,
    preference,
    preference_last_updated_at: '2026-11-13T04:30:00Z',
  }
}

async function savedLibrary(page: Page, initial: GamePreference | null = null) {
  let preference = initial
  const writes: { method: string; id: string; appId: number; value: unknown }[] = []
  await page.route(libraryRoute, (route) => {
    const id = new URL(route.request().url()).pathname.split('/')[3]
    return route.fulfill({
      json: libraryResponse(
        libraryGames.map((game) => (game.steam_app_id === 40 ? { ...game, preference } : game)),
        { steamId: id, preferencesAllowed: true },
      ),
    })
  })
  await page.route(preferenceRoute, (route) => {
    const path = new URL(route.request().url()).pathname.split('/')
    const id = path[3]
    const appId = Number(path[5])
    const method = route.request().method()
    const value = method === 'DELETE' ? null : route.request().postDataJSON().preference
    writes.push({ method, id, appId, value })
    preference = value
    return method === 'DELETE'
      ? route.fulfill({ status: 204 })
      : route.fulfill({ json: preferenceResponse(id, appId, preference!) })
  })
  return writes
}

test.beforeEach(async ({ page }) => {
  await page.route('https://shared.fastly.steamstatic.com/**', (route) => route.abort())
})

test('read-only states are visible without controls or automatic writes', async ({ page }) => {
  const writes: string[] = []
  page.on('request', (request) => {
    if (request.method() !== 'GET') writes.push(request.url())
  })
  await page.route(libraryRoute, (route) =>
    route.fulfill({
      json: libraryResponse(
        libraryGames.map((game, index) => ({
          ...game,
          preference: index === 0 ? 'liked' : index === 1 ? 'disliked' : null,
        })),
      ),
    }),
  )
  await page.goto(`/library/${USER_A}`)
  await expect(row(page)).toContainText('Liked')
  await expect(row(page, 20)).toContainText('Disliked')
  await expect(row(page, 30)).toContainText('Unrated')
  await expect(page.getByRole('group', { name: /^Preference for / })).toHaveCount(0)
  expect(writes).toEqual([])
})

test('like, switch, clear and reload preserve exact IDs and saved states', async ({ page }) => {
  const writes = await savedLibrary(page)
  await page.goto(`/library/${USER_A}`)
  await expect(like(page)).toHaveAttribute('aria-pressed', 'false')
  await like(page).focus()
  await page.keyboard.press('Enter')
  await expect(like(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(like(page)).toBeFocused()
  await expect(dislike(page)).toHaveAttribute('aria-pressed', 'false')
  await page.reload()
  await expect(like(page)).toHaveAttribute('aria-pressed', 'true')
  await dislike(page).click()
  await expect(dislike(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(like(page)).toHaveAttribute('aria-pressed', 'false')
  await dislike(page).click()
  await expect(dislike(page)).toHaveAttribute('aria-pressed', 'false')
  await expect(row(page)).toContainText('Unrated')
  await like(page).click()
  await expect(like(page)).toHaveAttribute('aria-pressed', 'true')
  await like(page).click()
  await expect(like(page)).toHaveAttribute('aria-pressed', 'false')
  expect(writes).toEqual([
    { method: 'PUT', id: USER_A, appId: 40, value: 'liked' },
    { method: 'PUT', id: USER_A, appId: 40, value: 'disliked' },
    { method: 'DELETE', id: USER_A, appId: 40, value: null },
    { method: 'PUT', id: USER_A, appId: 40, value: 'liked' },
    { method: 'DELETE', id: USER_A, appId: 40, value: null },
  ])
})

test('legacy neutral is preserved until explicitly cleared', async ({ page }) => {
  const writes = await savedLibrary(page, 'neutral')
  await page.goto(`/library/${USER_A}`)
  await expect(row(page)).toContainText('Neutral (legacy)')
  await expect(like(page)).toHaveAttribute('aria-pressed', 'false')
  await expect(dislike(page)).toHaveAttribute('aria-pressed', 'false')
  await row(page)
    .getByRole('button', { name: /^Clear legacy neutral preference/ })
    .click()
  await expect(row(page)).toContainText('Unrated')
  await expect(like(page)).toBeFocused()
  expect(writes).toEqual([{ method: 'DELETE', id: USER_A, appId: 40, value: null }])
})

test('pending writes retain confirmed state and block duplicates after leaving and returning', async ({
  page,
}) => {
  await savedLibrary(page, 'liked')
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  let count = 0
  await page.route(preferenceRoute, async (route) => {
    count++
    await pending
    await route.fulfill({ json: preferenceResponse(USER_A, 40, 'disliked') })
  })
  await page.goto(`/library/${USER_A}`)
  await dislike(page).click()
  await expect(row(page).getByText('Saving…')).toBeVisible()
  await expect(like(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(dislike(page)).toBeDisabled()
  await dislike(page).evaluate((button: HTMLButtonElement) => {
    button.click()
    button.click()
  })
  await page.getByRole('link', { name: 'Discover', exact: true }).click()
  await page.getByRole('link', { name: 'Library', exact: true }).click()
  await selectLibrary(page, USER_A)
  await expect(dislike(page)).toBeDisabled()
  expect(count).toBe(1)
  await page.route(libraryRoute, (route) =>
    route.fulfill({
      json: libraryResponse(
        libraryGames.map((game) => ({
          ...game,
          preference: game.steam_app_id === 40 ? 'disliked' : null,
        })),
        { preferencesAllowed: true },
      ),
    }),
  )
  release()
  await expect(dislike(page)).toHaveAttribute('aria-pressed', 'true')
  expect(count).toBe(1)
})

for (const [status, code, message] of [
  [403, 'preference_forbidden', /not permitted/],
  [404, 'game_not_owned', /no longer available/],
  [422, '', /could not be saved/],
  [503, 'database_unavailable', /temporarily unavailable/],
  [0, '', /Could not confirm/],
] as const) {
  test(`failed preference ${status} keeps prior state and retries only explicitly`, async ({
    page,
  }) => {
    await savedLibrary(page, 'liked')
    let attempts = 0
    await page.route(preferenceRoute, (route) => {
      attempts++
      if (attempts > 1) return route.fulfill({ json: preferenceResponse(USER_A, 40, 'disliked') })
      return status
        ? route.fulfill({ status, json: { detail: { code, message: 'Synthetic safe error' } } })
        : route.abort()
    })
    await page.goto(`/library/${USER_A}`)
    await dislike(page).click()
    await expect(row(page).getByRole('alert')).toContainText(message)
    await expect(like(page)).toHaveAttribute('aria-pressed', 'true')
    expect(attempts).toBe(1)
    await page.route(libraryRoute, (route) =>
      route.fulfill({
        json: libraryResponse(
          libraryGames.map((game) => ({
            ...game,
            preference: game.steam_app_id === 40 ? 'disliked' : null,
          })),
          { preferencesAllowed: true },
        ),
      }),
    )
    await row(page).getByRole('button', { name: 'Retry preference', exact: true }).click()
    await expect(dislike(page)).toHaveAttribute('aria-pressed', 'true')
    expect(attempts).toBe(2)
  })
}

test('switching libraries isolates pending and completed feedback', async ({ page }) => {
  let preference: GamePreference | null = null
  const reads: Record<string, number> = {}
  await page.route(libraryRoute, (route) => {
    const id = new URL(route.request().url()).pathname.split('/')[3]
    reads[id] = (reads[id] ?? 0) + 1
    return route.fulfill({
      json: libraryResponse(
        libraryGames.map((game) => ({
          ...game,
          preference: id === USER_A && game.steam_app_id === 40 ? preference : null,
        })),
        { steamId: id, preferencesAllowed: true },
      ),
    })
  })
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route(preferenceRoute, async (route) => {
    await pending
    preference = 'liked'
    await route.fulfill({ json: preferenceResponse(USER_A, 40, 'liked') })
  })
  await page.goto(`/library/${USER_A}`)
  await like(page).click()
  await selectLibrary(page, USER_B)
  await expect(like(page)).toBeEnabled()
  release()
  await expect(row(page)).not.toContainText('Preference saved.')
  await expect(like(page)).toHaveAttribute('aria-pressed', 'false')
  expect(reads[USER_B]).toBe(1)
  await selectLibrary(page, USER_A)
  await expect(like(page)).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(() => reads[USER_A]).toBe(2)
})

test('confirmed state remains visible if background library refresh fails', async ({ page }) => {
  let reads = 0
  await page.route(libraryRoute, (route) => {
    reads++
    return reads > 1
      ? route.fulfill({ status: 503, json: { detail: 'Unavailable' } })
      : route.fulfill({ json: libraryResponse(undefined, { preferencesAllowed: true }) })
  })
  await page.route(preferenceRoute, (route) =>
    route.fulfill({ json: preferenceResponse(USER_A, 40, 'liked') }),
  )
  await page.goto(`/library/${USER_A}`)
  await like(page).click()
  await expect(like(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByText(/displayed snapshot could not be refreshed/)).toBeVisible()
  await expect(row(page).getByText(/Preference saved/)).toBeVisible()
})

test('recommendation handoff fetches fresh server ordering without unrelated requests', async ({
  page,
}) => {
  const writes = await savedLibrary(page)
  let recommendationRequests = 0
  const unexpected: string[] = []
  page.on('request', (request) => {
    if (/\/library\/sync|\/explanations|\/auth/.test(request.url())) unexpected.push(request.url())
  })
  await page.route('**/api/recommendations', (route) => {
    recommendationRequests++
    return route.fulfill({
      json: {
        total: 2,
        limit: 20,
        games: [
          {
            ...recommendation,
            steam_app_id: 100,
            name: `First ${writes.length}`,
            hybrid_score: 0.9,
          },
          { ...recommendation, steam_app_id: 200, name: 'Second', hybrid_score: 0.8 },
        ],
      },
    })
  })
  await page.goto(`/library/${USER_A}`)
  await page.getByRole('link', { name: 'View recommendations' }).click()
  await expect(page.getByRole('button', { name: 'Inspect First 0' })).toBeVisible()
  await page.getByRole('link', { name: 'Library', exact: true }).click()
  await selectLibrary(page, USER_A)
  await like(page).click()
  await expect(like(page)).toHaveAttribute('aria-pressed', 'true')
  expect(recommendationRequests).toBe(1)
  await page.getByRole('link', { name: 'View recommendations' }).click()
  await expect(page.getByRole('button', { name: 'Inspect First 1' })).toBeVisible()
  expect(recommendationRequests).toBe(2)
  expect(unexpected).toEqual([])
})

test('controls retain keyboard focus and fit restrained layouts with all preference states', async ({
  page,
}, testInfo) => {
  await page.route(libraryRoute, (route) =>
    route.fulfill({
      json: libraryResponse(
        [
          ...libraryGames.map((game, index) => ({
            ...game,
            preference:
              index === 0
                ? ('liked' as const)
                : index === 1
                  ? ('disliked' as const)
                  : ('neutral' as const),
          })),
          { ...libraryGames[2], steam_app_id: 50, name: 'An unrated stored game' },
        ],
        { preferencesAllowed: true },
      ),
    }),
  )
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto(`/library/${USER_A}`)
  await like(page).focus()
  await expect(like(page)).toBeFocused()
  for (const [name, width, height] of [
    ['small-mobile', 320, 844],
    ['mobile', 390, 844],
    ['tablet', 768, 1024],
    ['desktop', 1440, 1080],
  ] as const) {
    await page.setViewportSize({ width, height })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await expect(like(page)).toHaveAttribute('aria-pressed', 'true')
    if (name === 'mobile' || name === 'desktop')
      await page.screenshot({
        path: testInfo.outputPath(`${name}-preferences.png`),
        animations: 'disabled',
        fullPage: true,
        mask: [page.locator('[data-private-steam-id]')],
        maskColor: '#303238',
      })
  }
})

test('confirmed writes cancel obsolete reads, patch same-user pages and invalidate recommendation limits only for that user', async ({
  page,
}) => {
  let release!: () => void
  const held = new Promise<void>((resolve) => {
    release = resolve
  })
  let reads = 0
  const cancelled: string[] = []
  page.on('requestfailed', (request) => cancelled.push(new URL(request.url()).pathname))
  await page.route(libraryRoute, async (route) => {
    const read = ++reads
    if (read === 1) await held
    await route.fulfill({
      json: libraryResponse(
        libraryGames.map((game) => ({ ...game, preference: read === 1 ? null : 'liked' })),
      ),
    })
  })
  await page.route('**/api/recommendations', async (route) => {
    await held
    await route.fulfill({
      json: { total: 1, limit: 20, games: [{ ...recommendation, name: 'Obsolete' }] },
    })
  })
  await page.route(preferenceRoute, (route) =>
    route.fulfill({ json: preferenceResponse(USER_A, 40, 'liked') }),
  )
  await page.goto('/tests/preferences.html')
  await expect(page.getByTestId('preference')).toHaveText('unrated')
  await page.getByRole('button', { name: 'Start old library read' }).click()
  await page.getByRole('button', { name: 'Start old recommendations' }).click()
  await expect(page.getByTestId('old-request')).toHaveText('pending')
  await page.getByRole('button', { name: 'Like test game' }).click()
  await expect(page.getByTestId('mutation')).toHaveText('saved')
  release()
  await expect(page.getByTestId('preference')).toHaveText('liked')
  await expect(page.getByTestId('old-request')).toHaveText('Baseline')
  await page.getByRole('button', { name: 'Read cache state' }).click()
  await expect(page.getByTestId('cache')).toHaveText(
    JSON.stringify({
      stale: [false, true, true, true, false, false],
      preferences: ['liked', 'liked', null],
      recommendationName: 'Baseline',
    }),
  )
  expect(reads).toBe(2)
  expect(cancelled).toContain(`/api/users/${USER_A}/library`)
  expect(cancelled).toContain('/api/recommendations')
})

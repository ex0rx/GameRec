import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { libraryGames, libraryResponse } from './library-fixtures'
import { USER_A, USER_B } from './recommendation-fixtures'

const libraryRoute = '**/api/users/*/library?**'
const syncRoute = '**/api/users/*/library/sync'
const rows = (page: Page) => page.locator('article.library-game-row')
const completed = (steamId = USER_A, partial = false) => ({
  steamid64: steamId,
  status: 'completed',
  processed_games: 3,
  total_games: 3,
  library_last_synced_at: '2026-11-13T04:30:00Z',
  metadata_enrichment: partial ? 'partial' : 'complete',
})

async function selectLibrary(page: Page, id: string) {
  await page.getByLabel('Steam ID64', { exact: true }).fill(id)
  await page.getByRole('button', { name: 'Load library', exact: true }).click()
}

test.beforeEach(async ({ page }) => {
  await page.route(libraryRoute, (route) =>
    route.fulfill({ json: libraryResponse(undefined, { syncAllowed: true }) }),
  )
  await page.route('https://shared.fastly.steamstatic.com/**', (route) => route.abort())
})

test('sync is hidden until the library explicitly permits it and never runs automatically', async ({
  page,
}) => {
  const writes: string[] = []
  page.on('request', (request) => {
    if (request.method() !== 'GET') writes.push(request.url())
  })
  await page.goto('/library')
  await expect(page.getByRole('button', { name: 'Sync Library' })).toHaveCount(0)
  await page.route(libraryRoute, (route) => route.fulfill({ json: libraryResponse() }))
  await selectLibrary(page, USER_A)
  await expect(rows(page)).toHaveCount(3)
  await expect(page.getByRole('button', { name: 'Sync Library' })).toHaveCount(0)
  expect(writes).toEqual([])
})

test('sync posts the exact string ID, retains rows, prevents duplicate clicks and survives leaving', async ({
  page,
}) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  let syncs = 0
  await page.route(syncRoute, async (route) => {
    syncs++
    expect(route.request().method()).toBe('POST')
    expect(new URL(route.request().url()).pathname).toBe(`/api/users/${USER_A}/library/sync`)
    expect(route.request().postData()).toBeNull()
    await pending
    await route.fulfill({ json: completed() })
  })
  await page.goto(`/library/${USER_A}`)
  const button = page.getByRole('button', { name: 'Sync Library', exact: true })
  await button.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('button', { name: 'Syncing…', exact: true })).toBeDisabled()
  await expect(rows(page)).toHaveCount(3)
  await page.getByRole('link', { name: 'Discover', exact: true }).click()
  await page.getByRole('link', { name: 'Library', exact: true }).click()
  await selectLibrary(page, USER_A)
  await expect(page.getByRole('button', { name: 'Syncing…', exact: true })).toBeDisabled()
  await expect(rows(page)).toHaveCount(3)
  expect(syncs).toBe(1)
  release()
  await expect(page.getByText(/Library synced\./)).toBeVisible()
  expect(syncs).toBe(1)
})

test('completed sync refreshes stored data and reports partial metadata without hiding saved rows', async ({
  page,
}, testInfo) => {
  let reads = 0
  const calls: string[] = []
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    if (path.startsWith('/api/')) calls.push(`${request.method()} ${path}`)
  })
  await page.route(libraryRoute, (route) => {
    reads++
    return route.fulfill({
      json: libraryResponse(
        reads === 1
          ? libraryGames
          : libraryGames.map((game, index) =>
              index === 0 ? { ...game, playtime_forever_minutes: 120 } : game,
            ),
        {
          syncAllowed: true,
          syncedAt: reads === 1 ? '2026-11-12T04:30:00Z' : completed().library_last_synced_at,
        },
      ),
    })
  })
  await page.route(syncRoute, (route) => route.fulfill({ json: completed(USER_A, true) }))
  await page.goto(`/library/${USER_A}`)
  await expect(rows(page)).toHaveCount(3)
  await page.getByRole('button', { name: 'Sync Library', exact: true }).click()
  await expect(page.getByText(/Library synced\./)).toBeVisible()
  await expect(page.getByText(/Some game metadata is still pending/)).toBeVisible()
  await expect(rows(page)).toHaveCount(3)
  await expect(rows(page).first()).toContainText('2 hrs')
  expect(reads).toBe(2)
  expect(
    calls.every((call) => /^(GET|POST) \/api\/users\/[0-9]{17}\/library(?:\/sync)?$/.test(call)),
  ).toBe(true)
  for (const [name, width, height] of [
    ['desktop', 1440, 1080],
    ['mobile', 390, 844],
  ] as const) {
    await page.setViewportSize({ width, height })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({
      path: testInfo.outputPath(`${name}-partial-sync.png`),
      animations: 'disabled',
      mask: [page.locator('[data-private-steam-id]')],
      maskColor: '#303238',
    })
  }
})

test('failed background refresh preserves snapshot and offers a read-only retry', async ({
  page,
}) => {
  let reads = 0
  let writes = 0
  await page.route(libraryRoute, (route) => {
    reads++
    return reads === 2
      ? route.fulfill({ status: 503, json: { detail: 'Unavailable' } })
      : route.fulfill({ json: libraryResponse(undefined, { syncAllowed: true }) })
  })
  await page.route(syncRoute, (route) => {
    writes++
    return route.fulfill({ json: completed() })
  })
  await page.goto(`/library/${USER_A}`)
  await page.getByRole('button', { name: 'Sync Library', exact: true }).click()
  await expect(page.getByText(/displayed snapshot could not be refreshed/)).toBeVisible()
  await expect(page.getByText(/Library synced\./)).toBeVisible()
  await expect(rows(page)).toHaveCount(3)
  await page.getByRole('button', { name: 'Refresh stored library', exact: true }).click()
  await expect(page.getByText(/displayed snapshot could not be refreshed/)).toHaveCount(0)
  expect(reads).toBe(3)
  expect(writes).toBe(1)
})

test('switching accounts isolates pending feedback and invalidates only the original account', async ({
  page,
}) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  const reads: Record<string, number> = {}
  await page.route(libraryRoute, (route) => {
    const id = new URL(route.request().url()).pathname.split('/')[3]
    reads[id] = (reads[id] ?? 0) + 1
    return route.fulfill({
      json: libraryResponse([libraryGames[id === USER_A ? 0 : 1]], {
        steamId: id,
        syncAllowed: true,
      }),
    })
  })
  await page.route(syncRoute, async (route) => {
    await pending
    await route.fulfill({ json: completed() })
  })
  await page.goto(`/library/${USER_A}`)
  await page.getByRole('button', { name: 'Sync Library', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Syncing…', exact: true })).toBeDisabled()
  await selectLibrary(page, USER_B)
  await expect(rows(page)).toContainText(libraryGames[1].name)
  await expect(page.getByRole('button', { name: 'Sync Library', exact: true })).toBeEnabled()
  release()
  await expect(page.getByText(/Library synced\./)).toHaveCount(0)
  expect(reads[USER_B]).toBe(1)
  await selectLibrary(page, USER_A)
  await expect(page.getByText(/Library synced\./)).toBeVisible()
  await expect.poll(() => reads[USER_A]).toBe(2)
  expect(reads[USER_B]).toBe(1)
})

test('sync invalidates all original-user pages and recommendation limits while refetching only active data', async ({
  page,
}) => {
  let reads = 0
  const requests: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/')) requests.push(request.url())
  })
  await page.route(libraryRoute, (route) => {
    reads++
    return route.fulfill({ json: libraryResponse() })
  })
  await page.route(syncRoute, (route) => route.fulfill({ json: completed() }))
  await page.goto('/tests/library-sync.html')
  await expect(page.getByTestId('library-count')).toHaveText('3')
  expect(reads).toBe(0)
  await page.getByRole('button', { name: 'Sync test library', exact: true }).click()
  await expect(page.getByTestId('sync-state')).toHaveText('completed')
  await page.getByRole('button', { name: 'Read cache state', exact: true }).click()
  await expect(page.getByTestId('cache-state')).toHaveText('[false,true,true,true,false,false]')
  expect(reads).toBe(1)
  expect(requests).toHaveLength(2)
  expect(requests.every((url) => url.includes(USER_A))).toBe(true)
})

for (const [status, code, message] of [
  [403, 'sync_forbidden', /not permitted/],
  [409, 'sync_in_progress', /already running/],
  [429, 'sync_cooldown', /wait 42 seconds/],
  [503, 'steam_unavailable', /visible library/],
  [502, 'steam_upstream_error', /Steam could not complete/],
  [504, 'sync_timeout', /may have saved data/],
  [503, 'database_unavailable', /temporarily unavailable/],
  [0, '', /connection was lost/],
] as const) {
  test(`sync error ${code || 'network'} retains the library and never retries automatically`, async ({
    page,
  }) => {
    let writes = 0
    await page.route(syncRoute, (route) => {
      writes++
      return status
        ? route.fulfill({
            status,
            headers: { 'Retry-After': '42' },
            json: { detail: { code, message: 'Synthetic safe error' } },
          })
        : route.abort()
    })
    await page.goto(`/library/${USER_A}`)
    await page.getByRole('button', { name: 'Sync Library', exact: true }).click()
    await expect(page.getByRole('alert')).toContainText(message)
    await expect(rows(page)).toHaveCount(3)
    expect(writes).toBe(1)
  })
}

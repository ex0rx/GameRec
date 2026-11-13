import { expect, test } from '@playwright/test'
import { USER_A, game } from './recommendation-fixtures'
import { mockCurrentUser } from './auth-fixtures'

const games = [
  {
    ...game,
    name: 'Z — A very long recommendation title with room for a subtitle and another adventure',
  },
  { ...game, steam_app_id: 30, name: null },
  { ...game, steam_app_id: 40, name: '   ' },
]
test.beforeEach(async ({ page }) => {
  await mockCurrentUser(page, USER_A)
  await page.route('**/api/recommendations', (route) =>
    route.fulfill({ json: { total: games.length, limit: 20, games } }),
  )
  await page.route('https://shared.fastly.steamstatic.com/**', (route) => route.abort())
})

test('signed-out Recommendations require sign-in without private requests', async ({ page }) => {
  await mockCurrentUser(page, null)
  const calls: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/recommendations') calls.push(request.url())
  })
  await page.goto('/discover')
  await page.getByRole('link', { name: 'Recommendations', exact: true }).click()
  await expect(page).toHaveURL('/recommendations')
  await expect(page.getByRole('link', { name: 'Recommendations', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('link', { name: 'Sign in with Steam' }).last()).toBeVisible()
  await expect(page.getByRole('article')).toHaveCount(0)
  expect(calls).toEqual([])
})

test('manual identity controls are absent in the signed-in recommendation journey', async ({ page }) => {
  await page.goto('/recommendations')
  await expect(page.getByRole('article')).toHaveCount(3)
  await expect(page.getByLabel('Development Steam ID')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Load recommendations' })).toHaveCount(0)
})

test('valid string ID renders server order and sparse metadata, without persistence or explanations', async ({
  page,
}) => {
  const apiPaths: string[] = []
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    if (path.startsWith('/api/') && !path.startsWith('/api/auth/')) apiPaths.push(path)
  })
  const request = page.waitForRequest('**/api/recommendations')
  await page.goto('/recommendations')
  expect((await request).postDataJSON()).toEqual({ steamid64: USER_A, limit: 20 })
  await expect(page.getByRole('article')).toHaveCount(3)
  await expect(page.locator('.game-title-row h3')).toHaveText([
    games[0].name!,
    'Steam app 30',
    'Steam app 40',
  ])
  await expect(page.locator('.result-position')).toHaveText(['01', '02', '03'])
  await expect(page.getByText('3 games returned')).toBeVisible()
  await expect(page.getByLabel('Development Steam ID')).toHaveCount(0)
  expect(new URL(page.url()).search).toBe('')
  expect(await page.evaluate(() => ({ ...localStorage, ...sessionStorage }))).toEqual({})
  await expect(page.getByRole('button', { name: 'Why this game?' })).toHaveCount(0)
  await expect(page.getByRole('img', { name: 'Steam app 30: artwork unavailable' })).toBeVisible()
  const details = page.getByRole('button', { name: 'View details', exact: true }).first()
  await details.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('link', { name: 'View on Steam', exact: true })).toHaveAttribute(
    'href',
    'https://store.steampowered.com/app/20/',
  )
  await expect(page.getByRole('button', { name: 'Close game details' })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: 'View on Steam', exact: true })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: 'Close game details' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(details).toBeFocused()
  expect(apiPaths).toEqual(['/api/recommendations'])
  await expect(page.getByText(/0\.7|70%|Review quality|Similarity score/)).toHaveCount(0)
})

test('loading disables refresh and explicit refresh failures hide stale results', async ({
  page,
}) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  let calls = 0
  let failRefresh = false
  await page.route('**/api/recommendations', async (route) => {
    calls++
    await pending
    if (failRefresh) await route.fulfill({ status: 503, json: { detail: 'Unavailable' } })
    else await route.fulfill({ json: { total: 1, limit: 20, games: [game] } })
  })
  await page.goto('/recommendations')
  await expect(page.getByLabel('Loading recommendations')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Refresh' })).toBeDisabled()
  release()
  await expect(page.getByRole('article')).toHaveCount(1)
  expect(calls).toBe(1)
  failRefresh = true
  await page.getByRole('button', { name: 'Refresh' }).click()
  await expect(page.getByRole('alert')).toContainText('temporarily unavailable')
  await expect(page.getByText('1 game returned')).toHaveCount(0)
  expect(calls).toBe(2)
})

test('empty recommendations do not imply an empty library', async ({ page }) => {
  await page.route('**/api/recommendations', (route) =>
    route.fulfill({ json: { total: 0, limit: 20, games: [] } }),
  )
  await page.goto('/recommendations')
  await expect(
    page.getByRole('heading', { name: 'No recommendations available yet' }),
  ).toBeVisible()
  await expect(page.getByText(/does not mean the Steam library is empty/)).toBeVisible()
})

for (const status of [404, 422, 503, 0]) {
  test(`recommendation error ${status} supports an explicit retry`, async ({ page }) => {
    let calls = 0
    await page.route('**/api/recommendations', (route) => {
      calls++
      return status
        ? route.fulfill({ status, json: { detail: 'Synthetic failure' } })
        : route.abort()
    })
    await page.goto('/recommendations')
    const message =
      status === 404
        ? 'No stored user'
        : status === 422
          ? 'Check'
          : status === 0
            ? 'Could not connect'
            : 'temporarily unavailable'
    await expect(page.getByRole('alert')).toContainText(message)
    expect(calls).toBe(1)
    await page.getByRole('button', { name: /Try again/ }).click()
    await expect.poll(() => calls).toBe(2)
    await expect(page.getByRole('article')).toHaveCount(0)
  })
}

for (const width of [320, 390, 768, 1440]) {
  test(`layout at ${width}px supports long titles, details and reduced motion`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/recommendations')
      await expect(page.getByRole('article')).toHaveCount(3)
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true)
    const card = page.getByRole('article').first()
    const bounds = await card.boundingBox()
    expect(bounds!.width).toBeGreaterThan(150)
    expect(await card.evaluate((element) => getComputedStyle(element).animationDuration)).toBe('0s')
    await page
      .getByRole('button', { name: /^Inspect/ })
      .first()
      .click()
    await expect(page.getByRole('dialog')).toBeVisible()
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true)
    await page.keyboard.press('Escape')
    await page.getByRole('link', { name: 'Discover', exact: true }).click()
    await page.getByRole('link', { name: 'Recommendations', exact: true }).click()
    await expect(page.getByRole('article')).toHaveCount(3)
  })
}

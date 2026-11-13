import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { libraryGames, libraryResponse, pagedLibraryGames } from './library-fixtures'
import { USER_A, USER_B, game } from './recommendation-fixtures'
import { changeCurrentUser, mockCurrentUser } from './auth-fixtures'

const libraryRoute = '**/api/users/*/library?**'
const rows = (page: Page) => page.locator('article.library-game-row')

test.beforeEach(async ({ page }) => {
  await mockCurrentUser(page, USER_A)
  await page.route(libraryRoute, (route) => route.fulfill({ json: libraryResponse() }))
  await page.route('https://shared.fastly.steamstatic.com/**', (route) => route.abort())
})

test('Library navigation loads the signed-in owner without an ID form', async ({ page }) => {
  const calls: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/users/')) calls.push(request.url())
  })
  await page.goto('/discover')
  expect(calls).toEqual([])
  await page.getByRole('link', { name: 'Library', exact: true }).click()
  await expect(page).toHaveURL('/library')
  await expect(page.getByRole('heading', { name: 'Game Library', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Library', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(rows(page)).toHaveCount(3)
  await expect(page.getByLabel('Steam ID64', { exact: true })).toHaveCount(0)
  expect(calls).toHaveLength(1)
})

test('invalid and other-account legacy routes never fetch a target library', async ({ page }) => {
  const calls: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/users/')) calls.push(request.url())
  })
  for (const id of ['invalid', '7656119000000000', '765611900000000001', USER_B]) {
    await page.goto(`/library/${id}`)
    await expect(rows(page)).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'Library unavailable' })).toBeVisible()
  }
  expect(calls).toEqual([])
})

test('verified string ID requests the stored library and preserves sparse server data and order', async ({
  page,
}) => {
  const calls: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/') && !request.url().includes('/auth/')) calls.push(request.url())
  })
  const pending = page.waitForRequest(libraryRoute)
  await page.goto('/library')
  const request = await pending
  expect(request.method()).toBe('GET')
  const url = new URL(request.url())
  expect(url.pathname).toBe(`/api/users/${USER_A}/library`)
  expect([...url.searchParams.entries()]).toEqual([
    ['limit', '50'],
    ['offset', '0'],
  ])
  await expect(page).toHaveURL('/library')
  await expect(rows(page)).toHaveCount(3)
  await expect(rows(page).getByRole('heading', { level: 3 })).toHaveText([
    libraryGames[0].name,
    libraryGames[1].name,
    'Steam app 30',
  ])
  await expect(rows(page).nth(0)).toContainText('Lifetime')
  await expect(rows(page).nth(0)).toContainText('1.5 hrs')
  await expect(rows(page).nth(0)).toContainText('Past 2 weeks')
  await expect(rows(page).nth(0)).toContainText('45 min')
  await expect(rows(page).nth(1)).toContainText('Not played')
  await expect(rows(page).nth(2)).toContainText('Playtime unavailable')
  await expect(rows(page).nth(2).getByText('Past 2 weeks')).toHaveCount(0)
  await expect(page.getByRole('img', { name: 'Steam app 30: artwork unavailable' })).toBeVisible()
  await expect(page.getByText(/2026/).filter({ hasText: /UTC/ })).toBeVisible()
  await expect(page.getByText('1–3 of 3 games')).toBeVisible()
  await expect(
    page
      .getByRole('navigation', { name: 'Library pages' })
      .getByRole('button', { name: 'Next', exact: true }),
  ).toBeDisabled()
  expect(await page.evaluate(() => ({ ...localStorage, ...sessionStorage }))).toEqual({})
  expect(calls).toHaveLength(1)
})

test('loading and null sync timestamp are represented without invented metadata', async ({
  page,
}) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route(libraryRoute, async (route) => {
    await pending
    await route.fulfill({ json: libraryResponse(undefined, { syncedAt: null }) })
  })
  await page.goto('/library')
  await expect(page.getByRole('status', { name: 'Loading library' })).toBeVisible()
  await expect(rows(page)).toHaveCount(0)
  release()
  await expect(rows(page)).toHaveCount(3)
  await expect(page.getByText('Sync time unavailable', { exact: true })).toBeVisible()
  await expect(
    page.getByText(/Review quality|Similarity score|Hybrid score|Total playtime/),
  ).toHaveCount(0)
})

test('pagination, partial final page, reload and browser history restore URL state', async ({
  page,
}) => {
  await page.route(libraryRoute, (route) => {
    const offset = Number(new URL(route.request().url()).searchParams.get('offset'))
    return route.fulfill({
      json: libraryResponse(pagedLibraryGames.slice(offset, offset + 50), { total: 65, offset }),
    })
  })
  await page.goto('/library')
  await expect(rows(page)).toHaveCount(50)
  await expect(rows(page).first()).toContainText('20 hrs')
  const navigation = page.getByRole('navigation', { name: 'Library pages' })
  await expect(navigation.getByRole('button', { name: 'Previous', exact: true })).toBeDisabled()
  await expect(page.getByText('1–50 of 65 games')).toBeVisible()
  await navigation.getByRole('button', { name: 'Next', exact: true }).click()
  await expect(page).toHaveURL(`/library?page=2`)
  await expect(rows(page)).toHaveCount(15)
  await expect(rows(page).first()).toContainText('Stored game 51')
  await expect(page.getByText('51–65 of 65 games')).toBeVisible()
  await expect(navigation.getByRole('button', { name: 'Next', exact: true })).toBeDisabled()
  await page.reload()
  await expect(rows(page)).toHaveCount(15)
  await page.goBack()
  await expect(rows(page)).toHaveCount(50)
  await page.goForward()
  await expect(rows(page)).toHaveCount(15)
  await navigation.getByRole('button', { name: 'Previous', exact: true }).click()
  await expect(rows(page)).toHaveCount(50)
})

test('malformed and unsafe page parameters request the first page', async ({ page }) => {
  for (const value of ['0', '-1', 'abc', '1.5', '9007199254740991', '999999999999999999999']) {
    const pending = page.waitForRequest(libraryRoute)
    await page.goto(`/library?page=${value}`)
    expect(new URL((await pending).url()).searchParams.get('offset')).toBe('0')
    await expect(rows(page)).toHaveCount(3)
  }
})

test('an out-of-range page offers a return to the first page', async ({ page }) => {
  await page.route(libraryRoute, (route) => {
    const offset = Number(new URL(route.request().url()).searchParams.get('offset'))
    return route.fulfill({
      json: libraryResponse(offset ? [] : libraryGames, { total: 3, offset }),
    })
  })
  await page.goto(`/library?page=3`)
  await expect(rows(page)).toHaveCount(0)
  await page.getByRole('link', { name: 'Return to first page', exact: true }).click()
  await expect(rows(page)).toHaveCount(3)
  expect(new URL(page.url()).searchParams.has('page')).toBe(false)
})

test('session identity changes cancel old library reads and isolate late responses', async ({ page }) => {
  const session = await mockCurrentUser(page, USER_A)
  let release!: () => void
  const pending = new Promise<void>((resolve) => { release = resolve })
  await page.route(libraryRoute, async (route) => {
    if (route.request().url().includes(USER_A)) {
      await pending
      await route.fulfill({ json: libraryResponse([libraryGames[0]]) }).catch(() => {})
    } else {
      await route.fulfill({ json: libraryResponse([libraryGames[1]], { steamId: USER_B }) })
    }
  })
  await page.goto('/library')
  await expect(page.getByRole('status', { name: 'Loading library' })).toBeVisible()
  const cancelled = page.waitForEvent('requestfailed', (request) => request.url().includes(`/users/${USER_A}/library`))
  await changeCurrentUser(page, session, USER_B)
  await expect(rows(page)).toHaveCount(1)
  await expect(rows(page).first()).toContainText(libraryGames[1].name)
  release()
  await cancelled
  await expect(rows(page).first()).not.toContainText(libraryGames[0].name)
})

test('empty storage does not claim that the Steam account owns no games', async ({ page }) => {
  await page.route(libraryRoute, (route) => route.fulfill({ json: libraryResponse([]) }))
  await page.goto('/library')
  await expect(
    page.getByRole('heading', { name: 'No games stored for this library' }),
  ).toBeVisible()
  await expect(rows(page)).toHaveCount(0)
  await expect(page.getByText(/does not establish whether your Steam library is empty/)).toBeVisible()
})

for (const status of [404, 503, 0]) {
  test(`library failure ${status} is distinct and retries only on user action`, async ({
    page,
  }) => {
    let calls = 0
    await page.route(libraryRoute, (route) => {
      calls++
      return status
        ? route.fulfill({ status, json: { detail: 'Synthetic failure' } })
        : route.abort()
    })
    await page.goto('/library')
    await expect(
      page.getByRole('heading', {
        name: status === 404 ? 'No stored library found' : 'Library couldn’t be loaded',
      }),
    ).toBeVisible()
    await expect(rows(page)).toHaveCount(0)
    await expect(page.getByRole('link', { name: 'View recommendations', exact: true })).toHaveCount(
      0,
    )
    expect(calls).toBe(1)
    await page.getByRole('button', { name: /Try again/ }).click()
    await expect.poll(() => calls).toBe(2)
  })
}

test('game inspection is keyboard accessible and makes no explanation or mutation requests', async ({
  page,
}) => {
  const calls: string[] = []
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/') && !url.pathname.startsWith('/api/auth/')) calls.push(`${request.method()} ${url.pathname}`)
  })
  await page.goto('/library')
  await expect(rows(page)).toHaveCount(3)
  const inspect = page.getByRole('button', { name: `Inspect ${libraryGames[0].name}`, exact: true })
  await inspect.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('dialog')).not.toContainText('recommendation set')
  await expect(page.getByRole('button', { name: 'Close game details' })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: 'View on Steam', exact: true })).toBeFocused()
  await expect(page.getByRole('link', { name: 'View on Steam', exact: true })).toHaveAttribute(
    'href',
    'https://store.steampowered.com/app/40/',
  )
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: 'Close game details' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(inspect).toBeFocused()
  expect(calls).toEqual([`GET /api/users/${USER_A}/library`])
})

test('Library link and reload use the session account without a URL identity', async ({
  page,
}) => {
  let calls = 0
  await page.route('**/api/recommendations', (route) => {
    calls++
    return route.fulfill({ json: { total: 1, limit: 20, games: [game] } })
  })
  await page.goto('/library')
  await expect(rows(page)).toHaveCount(3)
  expect(calls).toBe(0)
  const request = page.waitForRequest('**/api/recommendations')
  await page.getByRole('link', { name: 'View recommendations', exact: true }).click()
  expect((await request).postDataJSON()).toEqual({ steamid64: USER_A, limit: 20 })
  await expect(page).toHaveURL('/recommendations')
  await expect(page.getByRole('article')).toHaveCount(1)
  expect(await page.evaluate(() => window.history.state.usr)).toBeNull()
  expect(await page.evaluate(() => ({ ...localStorage, ...sessionStorage }))).toEqual({})
  await page.reload()
  await expect(page.getByRole('article')).toHaveCount(1)
  expect(calls).toBe(2)
})

for (const width of [320, 390, 768, 1440]) {
  test(`library and details fit ${width}px with long titles and reduced motion`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/library')
    await expect(rows(page)).toHaveCount(3)
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true)
    expect(
      await rows(page)
        .first()
        .evaluate((element) => getComputedStyle(element).animationDuration),
    ).toBe('0s')
    await page
      .getByRole('button', { name: /^Inspect/ })
      .first()
      .click()
    await expect(page.getByRole('dialog')).toBeVisible()
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true)
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
  })
}

test('library client options and query keys preserve identity and isolate pages', async ({
  page,
}) => {
  const requests: URL[] = []
  await page.route(libraryRoute, (route) => {
    requests.push(new URL(route.request().url()))
    return route.fulfill({ json: libraryResponse() })
  })
  await page.goto('/discover')
  const keys = await page.evaluate(
    async ({ first, second }) => {
      const clientUrl = '/src/api/client.ts'
      const hookUrl = '/src/features/library/useSteamLibrary.ts'
      const { getSteamLibrary } = (await import(
        /* @vite-ignore */ clientUrl
      )) as typeof import('../src/api/client')
      const { libraryQueryKey } = (await import(
        /* @vite-ignore */ hookUrl
      )) as typeof import('../src/features/library/useSteamLibrary')
      await getSteamLibrary(` ${first} `)
      await getSteamLibrary(first, { limit: 20, offset: 40 })
      return [
        libraryQueryKey(` ${first} `),
        libraryQueryKey(first, 50, 0),
        libraryQueryKey(first, 50, 50),
        libraryQueryKey(second, 50, 0),
      ]
    },
    { first: USER_A, second: USER_B },
  )
  expect(requests.map((url) => url.pathname)).toEqual([
    `/api/users/${USER_A}/library`,
    `/api/users/${USER_A}/library`,
  ])
  expect(requests.map((url) => url.search)).toEqual(['?limit=50&offset=0', '?limit=20&offset=40'])
  expect(keys[0]).toEqual(['steam-library', USER_A, { limit: 50, offset: 0 }])
  expect(keys[0]).toEqual(keys[1])
  expect(keys[0]).not.toEqual(keys[2])
  expect(keys[0]).not.toEqual(keys[3])
})

test('router-state account cannot override the authenticated recommendation identity', async ({ page }) => {
  const bodies: unknown[] = []
  await page.route('**/api/recommendations', (route) => {
    bodies.push(route.request().postDataJSON())
    return route.fulfill({ json: { total: 1, limit: 20, games: [game] } })
  })
  await page.addInitScript((steamId) => {
    window.history.replaceState({ ...window.history.state, usr: { steamId } }, '')
  }, USER_B)
  await page.goto('/recommendations')
  await expect(page.getByRole('article')).toHaveCount(1)
  expect(bodies).toEqual([{ steamid64: USER_A, limit: 20 }])
})

test('library loading and empty states have desktop and mobile visual evidence', async ({
  page,
}, testInfo) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route(libraryRoute, async (route) => {
    await pending
    await route.fulfill({ json: libraryResponse([]) })
  })
  await page.goto('/library')
  await expect(page.getByRole('status', { name: 'Loading library' })).toBeVisible()
  for (const [name, width, height] of [
    ['desktop', 1440, 1080],
    ['mobile', 390, 844],
  ] as const) {
    await page.setViewportSize({ width, height })
    await page.screenshot({
      path: testInfo.outputPath(`${name}-loading.png`),
      animations: 'disabled',
      mask: [page.locator('[data-private-steam-id]')],
      maskColor: '#303238',
    })
  }
  release()
  await expect(
    page.getByRole('heading', { name: 'No games stored for this library' }),
  ).toBeVisible()
  for (const [name, width, height] of [
    ['desktop', 1440, 1080],
    ['mobile', 390, 844],
  ] as const) {
    await page.setViewportSize({ width, height })
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true)
    await page.screenshot({
      path: testInfo.outputPath(`${name}-empty.png`),
      animations: 'disabled',
      mask: [page.locator('[data-private-steam-id]')],
      maskColor: '#303238',
    })
  }
})

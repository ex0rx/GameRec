import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { mockCurrentUser, changeCurrentUser } from './auth-fixtures'
import { libraryResponse } from './library-fixtures'
import { game, USER_A, USER_B } from './recommendation-fixtures'

const signIn = (page: Page) => page.getByRole('link', { name: 'Sign in with Steam', exact: true })
const rows = (page: Page) => page.locator('article.library-game-row')

async function privateResponses(page: Page) {
  const requests: { method: string; path: string; body: unknown }[] = []
  await page.route(/\/api\/users\/[0-9]+\/library(?:\?|$)/, (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    requests.push({ method: request.method(), path, body: null })
    const identity = path.split('/')[3]
    return route.fulfill({
      json: libraryResponse(undefined, {
        steamId: identity,
        syncAllowed: true,
        preferencesAllowed: true,
      }),
    })
  })
  await page.route('**/api/recommendations', (route) => {
    const request = route.request()
    requests.push({ method: request.method(), path: '/api/recommendations', body: request.postDataJSON() })
    return route.fulfill({ json: { total: 1, limit: 20, games: [game] } })
  })
  await page.route('https://shared.fastly.steamstatic.com/**', (route) => route.abort())
  return requests
}

test('signed-out discovery remains public and Steam sign-in is top-level navigation', async ({ page }) => {
  await mockCurrentUser(page, null)
  const calls = await privateResponses(page)
  let documentNavigation = false
  await page.route('**/api/auth/steam/login', (route) => {
    documentNavigation = route.request().isNavigationRequest()
    return route.fulfill({ contentType: 'text/html', body: '<h1>Mock Steam redirect</h1>' })
  })
  await page.goto('/discover')
  await expect(signIn(page)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Search games.', exact: true })).toBeVisible()
  expect(calls).toEqual([])
  await signIn(page).click()
  await expect(page).toHaveURL('/api/auth/steam/login')
  expect(documentNavigation).toBe(true)
})

for (const path of ['/library', '/recommendations']) {
  test(`signed-out ${path} has a sign-in requirement without private requests`, async ({ page }) => {
    await mockCurrentUser(page, null)
    const calls = await privateResponses(page)
    await page.goto(path)
    await expect(signIn(page).first()).toBeVisible()
    await expect(page.getByRole('heading', { name: /sign in/i })).toBeVisible()
    await expect(page.getByLabel('Steam ID64', { exact: true })).toHaveCount(0)
    await expect(page.getByLabel('Development Steam ID')).toHaveCount(0)
    expect(calls).toEqual([])
  })
}

test('session loading hides private content until the verified identity resolves', async ({ page }) => {
  let release!: () => void
  const held = new Promise<void>((resolve) => { release = resolve })
  await page.route('**/api/auth/me', async (route) => {
    await held
    await route.fulfill({ json: { steamid64: USER_A } })
  })
  const calls = await privateResponses(page)
  await page.goto('/library')
  await expect(rows(page)).toHaveCount(0)
  expect(calls).toEqual([])
  release()
  await expect(rows(page)).toHaveCount(3)
  await expect(page.getByText('Signed in with Steam', { exact: true })).toBeVisible()
})

for (const failure of ['server', 'network'] as const) {
  test(`unexpected authentication ${failure} failure is retryable and keeps discovery available`, async ({ page }) => {
    let count = 0
    let recovered = false
    await page.route('**/api/auth/me', (route) => {
      count++
      if (recovered) return route.fulfill({ json: { steamid64: USER_A } })
      return failure === 'server' ? route.fulfill({ status: 503 }) : route.abort()
    })
    const calls = await privateResponses(page)
    await page.goto('/library')
    await expect(page.getByRole('alert').first()).toBeVisible()
    expect(calls).toEqual([])
    expect(count).toBe(1)
    recovered = true
    await page.getByRole('button', { name: /try again|retry/i }).first().click()
    await expect(rows(page)).toHaveCount(3)
    await page.getByRole('link', { name: 'Discover', exact: true }).click()
    await expect(page).toHaveURL('/discover')
  })
}

test('sidebar Library and Recommendations automatically use the verified string identity', async ({ page }) => {
  await mockCurrentUser(page, USER_A)
  const calls = await privateResponses(page)
  await page.goto('/discover')
  await expect(page.getByRole('button', { name: 'Sign out', exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Library', exact: true }).click()
  await expect(rows(page)).toHaveCount(3)
  await expect(page).toHaveURL('/library')
  await page.getByRole('link', { name: 'Recommendations', exact: true }).click()
  await expect(page.getByRole('article')).toHaveCount(1)
  expect(calls.find((call) => call.path === '/api/recommendations')?.body).toEqual({ steamid64: USER_A, limit: 20 })
  expect(calls.filter((call) => call.path.includes('/users/')).every((call) => call.path.includes(USER_A))).toBe(true)
  await expect(page.getByLabel('Development Steam ID')).toHaveCount(0)
  await expect(page.locator('body')).not.toContainText(USER_A)
  await page.reload()
  await expect(page.getByRole('article')).toHaveCount(1)
})

test('login return resumes only the requested safe page once', async ({ page }) => {
  const session = await mockCurrentUser(page, null)
  await privateResponses(page)
  await page.route('**/api/auth/steam/login', (route) => {
    session.steamId = USER_A
    return route.fulfill({ status: 302, headers: { Location: '/discover' } })
  })
  await page.goto('/recommendations')
  await signIn(page).last().click()
  await expect(page).toHaveURL('/recommendations')
  await expect(page.getByRole('article')).toHaveCount(1)
  expect(await page.evaluate(() => sessionStorage.getItem('gamerec.auth.returnTo'))).toBeNull()
  expect(await page.evaluate(() => ({ ...localStorage, ...sessionStorage }))).toEqual({})
})

for (const entry of [
  { path: 'https://untrusted.invalid/', createdAt: Date.now() },
  { path: '//untrusted.invalid/', createdAt: Date.now() },
  { path: `/library/${USER_B}`, createdAt: Date.now() },
  { path: '/library', createdAt: 1 },
]) {
  test(`unsafe or expired login destination ${entry.path} is discarded`, async ({ page }) => {
    await mockCurrentUser(page, USER_A)
    await page.addInitScript((value) => sessionStorage.setItem('gamerec.auth.returnTo', JSON.stringify(value)), entry)
    await page.goto('/discover')
    await expect(page.getByText('Signed in with Steam', { exact: true })).toBeVisible()
    await expect(page).toHaveURL('/discover')
    expect(await page.evaluate(() => sessionStorage.getItem('gamerec.auth.returnTo'))).toBeNull()
  })
}

test('protected 401 removes the private presentation and prompts sign-in', async ({ page }) => {
  const session = await mockCurrentUser(page, USER_A)
  await privateResponses(page)
  await page.goto('/library')
  await expect(rows(page)).toHaveCount(3)
  session.steamId = null
  await page.route('**/api/users/*/preferences/*', (route) => route.fulfill({ status: 401 }))
  await rows(page).first().getByRole('button', { name: /^Like / }).click()
  await expect(rows(page)).toHaveCount(0)
  await expect(signIn(page).first()).toBeVisible()
})

test('account revalidation never leaves the previous account library visible', async ({ page }) => {
  const session = await mockCurrentUser(page, USER_A)
  await page.route(/\/api\/users\/[0-9]+\/library(?:\?|$)/, (route) => {
    const identity = new URL(route.request().url()).pathname.split('/')[3]
    return route.fulfill({ json: libraryResponse([{ steam_app_id: 10, name: identity === USER_A ? 'Account A only' : 'Account B only', playtime_forever_minutes: 1, playtime_2weeks_minutes: null, preference: null }], { steamId: identity }) })
  })
  await page.goto('/library')
  await expect(page.getByRole('heading', { name: 'Account A only', exact: true })).toBeVisible()
  await changeCurrentUser(page, session, USER_B)
  await expect(page.getByRole('heading', { name: 'Account B only', exact: true })).toBeVisible()
  await expect(page.getByText('Account A only', { exact: true })).toHaveCount(0)
})

test('successful logout clears private pages, persists signed-out refresh and preserves public discovery', async ({ page }) => {
  const session = await mockCurrentUser(page, USER_A)
  const calls = await privateResponses(page)
  await page.route('**/api/auth/logout', (route) => {
    session.steamId = null
    return route.fulfill({ status: 204 })
  })
  await page.goto('/library')
  await expect(rows(page)).toHaveCount(3)
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  await expect(page).toHaveURL('/discover')
  await expect(signIn(page)).toBeVisible()
  const previousCount = calls.length
  await page.getByRole('link', { name: 'Library', exact: true }).click()
  await expect(rows(page)).toHaveCount(0)
  await page.reload()
  await expect(signIn(page).first()).toBeVisible()
  expect(calls.length).toBe(previousCount)
})

test('failed logout revalidates rather than claiming the backend session ended', async ({ page }) => {
  await mockCurrentUser(page, USER_A)
  await privateResponses(page)
  await page.route('**/api/auth/logout', (route) => route.fulfill({ status: 503 }))
  await page.goto('/library')
  await expect(rows(page)).toHaveCount(3)
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  await expect(page.getByRole('alert').first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Sign out', exact: true })).toBeVisible()
  await expect(signIn(page)).toHaveCount(0)
})

test('authenticated fetches explicitly include browser-managed HttpOnly cookies and native CSRF origin', async ({ page, context }) => {
  const syntheticCookie = 'synthetic-http-only-session'
  await context.addCookies([{ name: 'gamerec_session', value: syntheticCookie, url: 'http://frontend:5173', httpOnly: true, sameSite: 'Lax' }])
  await mockCurrentUser(page, USER_A)
  await privateResponses(page)
  await page.addInitScript(() => {
    const original = window.fetch
    const recorded: { path: string; credentials: RequestCredentials | undefined }[] = []
    Object.assign(window, { authFetchCalls: recorded })
    window.fetch = (input, init) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (url.startsWith('/api/')) recorded.push({ path: url, credentials: init?.credentials })
      return original(input, init)
    }
  })
  let observedCookie = ''
  let observedOrigin = ''
  await page.route('**/api/users/*/preferences/*', async (route) => {
    observedCookie = (await route.request().allHeaders()).cookie ?? ''
    observedOrigin = (await route.request().allHeaders()).origin ?? ''
    return route.fulfill({ json: { steamid64: USER_A, steam_app_id: 40, preference: 'liked', preference_last_updated_at: '2026-11-13T12:00:00Z' } })
  })
  await page.goto('/library')
  await expect(rows(page)).toHaveCount(3)
  await rows(page).first().getByRole('button', { name: /^Like / }).click()
  await expect.poll(() => observedCookie).toContain(`gamerec_session=${syntheticCookie}`)
  expect(observedOrigin).toBe('http://frontend:5173')
  const calls = await page.evaluate(() => (window as unknown as { authFetchCalls: { path: string; credentials: string }[] }).authFetchCalls)
  expect(calls.every((call) => call.credentials === 'include')).toBe(true)
  await expect(page.locator('body')).not.toContainText(syntheticCookie)
})

for (const code of ['account_forbidden', 'csrf_failed']) {
  test(`a ${code} response remains a permission error without logging out`, async ({ page }) => {
    await mockCurrentUser(page, USER_A)
    await privateResponses(page)
    await page.route('**/api/users/*/preferences/*', (route) =>
      route.fulfill({ status: 403, json: { detail: { code } } }),
    )
    await page.goto('/library')
    await expect(rows(page)).toHaveCount(3)
    await rows(page).first().getByRole('button', { name: /^Like / }).click()
    await expect(page.getByRole('alert').first()).toBeVisible()
    await expect(page.getByRole('button', { name: 'Sign out', exact: true })).toBeVisible()
    await expect(rows(page)).toHaveCount(3)
    await expect(signIn(page)).toHaveCount(0)
  })
}

test('logout pending blocks private presentation until the server confirms success', async ({ page }) => {
  const session = await mockCurrentUser(page, USER_A)
  await privateResponses(page)
  let release!: () => void
  const held = new Promise<void>((resolve) => { release = resolve })
  await page.route('**/api/auth/logout', async (route) => {
    await held
    session.steamId = null
    return route.fulfill({ status: 204 })
  })
  await page.goto('/library')
  await expect(rows(page)).toHaveCount(3)
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  await expect(rows(page)).toHaveCount(0)
  await expect(signIn(page)).toHaveCount(0)
  release()
  await expect(page).toHaveURL('/discover')
  await expect(signIn(page)).toBeVisible()
})

test('an uncertain logout response rechecks the server and recognizes a revoked session', async ({ page }) => {
  const session = await mockCurrentUser(page, USER_A)
  await privateResponses(page)
  await page.route('**/api/auth/logout', (route) => {
    session.steamId = null
    return route.abort()
  })
  await page.goto('/library')
  await expect(rows(page)).toHaveCount(3)
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  await expect(rows(page)).toHaveCount(0)
  await expect(signIn(page).first()).toBeVisible()
})

for (const width of [320, 390, 768, 1440]) {
  test(`account controls and protected states are keyboard accessible at ${width}px`, async ({ page }) => {
    const session = await mockCurrentUser(page, USER_A)
    await privateResponses(page)
    await page.setViewportSize({ width, height: width < 768 ? 844 : 1000 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/library')
    await expect(rows(page)).toHaveCount(3)
    await expect(page.getByText('Signed in with Steam', { exact: true })).toBeVisible()
    const signOut = page.getByRole('button', { name: 'Sign out', exact: true })
    await signOut.focus()
    await expect(signOut).toBeFocused()
    await page.keyboard.press('Tab')
    await expect(page.getByRole('link', { name: 'Discover', exact: true })).toBeFocused()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    if (width === 390 || width === 1440) {
      await page.screenshot({ path: `live-results/auth/phase2c/signed-in-${width}.png`, animations: 'disabled' })
    }
    await changeCurrentUser(page, session, null)
    await expect(rows(page)).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'Sign in to continue' })).toBeVisible()
    await signIn(page).last().focus()
    await expect(signIn(page).last()).toBeFocused()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    if (width === 390 || width === 1440) {
      await page.screenshot({ path: `live-results/auth/phase2c/signed-out-${width}.png`, animations: 'disabled' })
    }
  })
}

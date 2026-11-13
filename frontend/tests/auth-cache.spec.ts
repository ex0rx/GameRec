import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { libraryResponse, libraryGames } from './library-fixtures'
import { USER_A, USER_B, game } from './recommendation-fixtures'

async function changeSession(page: Page, identity: string | null) {
  await page.evaluate(async (value) => {
    const path = '/tests/auth-cache-harness.tsx'
    const harness = await import(path) as typeof import('./auth-cache-harness')
    harness.changeHarnessSession(value)
  }, identity)
}

async function cacheState(page: Page) {
  return page.evaluate(async () => {
    const path = '/tests/auth-cache-harness.tsx'
    const harness = await import(path) as typeof import('./auth-cache-harness')
    return harness.readCacheState()
  })
}

async function mockPrivateReads(page: Page) {
  await page.route(/\/api\/users\/[0-9]+\/library(?:\?|$)/, (route) => {
    const id = new URL(route.request().url()).pathname.split('/')[3]
    return route.fulfill({ json: libraryResponse([{ ...libraryGames[0], name: id === USER_A ? 'Current account A' : 'Current account B' }], { steamId: id, preferencesAllowed: true }) })
  })
  await page.route('**/api/recommendations', (route) => {
    const id = route.request().postDataJSON().steamid64
    return route.fulfill({ json: { total: 1, limit: 20, games: [{ ...game, name: id === USER_A ? 'Recommendation A' : 'Recommendation B' }] } })
  })
}

for (const status of [200, 401]) {
  test(`a delayed old-account preference ${status} cannot patch or expire a new A→B→A session`, async ({ page }) => {
    await mockPrivateReads(page)
    let release!: () => void
    const held = new Promise<void>((resolve) => { release = resolve })
    let entered!: () => void
    const started = new Promise<void>((resolve) => { entered = resolve })
    await page.route('**/api/users/*/preferences/40', async (route) => {
      entered()
      await held
      return status === 401
        ? route.fulfill({ status: 401 })
        : route.fulfill({ json: { steamid64: USER_A, steam_app_id: 40, preference: 'liked', preference_last_updated_at: '2026-11-13T12:00:00Z' } })
    })
    await page.goto('/tests/auth-cache.html')
    await expect(page.getByTestId('library-name')).toHaveText('Current account A')
    await page.getByRole('button', { name: 'Like test game' }).click()
    await started
    await changeSession(page, USER_B)
    await expect(page.getByTestId('library-name')).toHaveText('Current account B')
    await changeSession(page, USER_A)
    await expect(page.getByTestId('library-name')).toHaveText('Current account A')
    release()
    await expect(page.getByTestId('mutation-state')).toHaveText('idle')
    await expect(page.getByTestId('preference')).toHaveText('unrated')
    const state = await cacheState(page)
    expect(state.identity).toBe(USER_A)
    expect(state.privateMutations).toBe(0)
    expect(state.publicData).toEqual({ query: 'public discovery remains cached' })
  })
}

test('a late cancelled read cannot replace the same account after logout and re-entry', async ({ page }) => {
  let first = true
  let release!: () => void
  const held = new Promise<void>((resolve) => { release = resolve })
  let entered!: () => void
  const started = new Promise<void>((resolve) => { entered = resolve })
  await mockPrivateReads(page)
  await page.route('**/api/recommendations', async (route) => {
    const id = route.request().postDataJSON().steamid64
    if (id === USER_A && first) {
      first = false
      entered()
      await held
      await route.fulfill({ json: { total: 1, limit: 20, games: [{ ...game, name: 'Obsolete recommendation A' }] } }).catch(() => {})
      return
    }
    return route.fulfill({ json: { total: 1, limit: 20, games: [{ ...game, name: 'Fresh recommendation A' }] } })
  })
  await page.goto('/tests/auth-cache.html')
  await started
  await changeSession(page, null)
  await changeSession(page, USER_A)
  await expect(page.getByTestId('recommendation-name')).toHaveText('Fresh recommendation A')
  release()
  await expect(page.getByTestId('recommendation-name')).toHaveText('Fresh recommendation A')
  expect(JSON.stringify(await cacheState(page))).not.toContain('Obsolete recommendation A')
})

test('expiry removes private query and mutation data while preserving public discovery cache', async ({ page }) => {
  await mockPrivateReads(page)
  await page.goto('/tests/auth-cache.html')
  await expect(page.getByTestId('library-name')).toHaveText('Current account A')
  await expect(page.getByTestId('recommendation-name')).toHaveText('Recommendation A')
  await page.evaluate(async () => {
    const path = '/tests/auth-cache-harness.tsx'
    const harness = await import(path) as typeof import('./auth-cache-harness')
    harness.expireHarnessSession()
  })
  await expect(page.getByTestId('library-name')).toHaveText('')
  await expect(page.getByTestId('recommendation-name')).toHaveText('')
  const state = await cacheState(page)
  expect(state.identity).toBeNull()
  expect(state.privateData).toEqual([])
  expect(state.privateMutations).toBe(0)
  expect(state.publicData).toEqual({ query: 'public discovery remains cached' })
})

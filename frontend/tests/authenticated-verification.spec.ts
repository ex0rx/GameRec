import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const owner = '76561190000000001' // Synthetic only.

async function setup(page: Page, failure?: 'write' | 'cleanup' | 'anonymous') {
  const state = {
    signedIn: failure !== 'anonymous',
    preference: null as string | null,
    writes: [] as string[],
    syncs: 0,
  }
  let synced = false
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      const request = route.request()
      const path = new URL(request.url()).pathname.replace(/^\/api/, '')
      const method = request.method()
      if (path === '/auth/me') {
        return route.fulfill({
          status: state.signedIn ? 200 : 401,
          json: state.signedIn
            ? { steamid64: owner }
            : { detail: { code: 'authentication_required' } },
        })
      }
      if (path === '/auth/logout') {
        state.signedIn = false
        return route.fulfill({ status: 204 })
      }
      if (!state.signedIn) return route.fulfill({ status: 401 })
      const other = path.startsWith('/users/')
        ? !path.startsWith(`/users/${owner}/`)
        : path === '/recommendations' && request.postDataJSON().steamid64 !== owner
      if (other) return route.fulfill({ status: 403 })
      if (path.endsWith('/library/sync')) {
        state.syncs++
        synced = true
        return route.fulfill({
          json: {
            steamid64: owner,
            status: 'completed',
            processed_games: 1,
            total_games: 1,
            library_last_synced_at: '2026-11-13T12:00:00Z',
            metadata_enrichment: 'partial',
          },
        })
      }
      if (path.endsWith('/library')) {
        return route.fulfill({
          json: {
            steamid64: owner,
            total_games: 1,
            limit: 100,
            offset: 0,
            library_last_synced_at: synced ? '2026-11-13T12:00:00Z' : null,
            sync_allowed: true,
            preference_writes_allowed: true,
            games: [
              {
                steam_app_id: 10,
                name: 'Synthetic game',
                preference: state.preference,
                playtime_forever_minutes: 500,
                playtime_2weeks_minutes: null,
              },
            ],
          },
        })
      }
      if (path.endsWith('/preferences/10')) {
        if (method === 'DELETE') {
          state.writes.push('clear')
          if (failure === 'cleanup') return route.fulfill({ status: 503 })
          state.preference = null
          return route.fulfill({ status: 204 })
        }
        state.preference = request.postDataJSON().preference
        state.writes.push(state.preference!)
        if (failure === 'write') return route.fulfill({ status: 503 })
        return route.fulfill({
          json: {
            steamid64: owner,
            steam_app_id: 10,
            preference: state.preference,
            preference_last_updated_at: '2026-11-13T12:00:00Z',
          },
        })
      }
      if (path === '/recommendations') {
        const score =
          state.preference === 'liked' ? 0.8 : state.preference === 'disliked' ? 0.6 : 0.7
        return route.fulfill({
          json: {
            total: 1,
            limit: 20,
            games: [
              {
                steam_app_id: 20,
                name: 'Synthetic recommendation',
                similarity_score: score,
                popularity_score: 0.7,
                review_quality: 0.9,
                hybrid_score: score,
                affinity_score: score,
                recency_score: 0.4,
              },
            ],
          },
        })
      }
      throw new Error('Unexpected endpoint in browser-only verification')
    },
  )
  await page.goto('/discover')
  return state
}

async function verify(page: Page) {
  return page.evaluate(async () => {
    const path = '/scripts/verify_authenticated_account.ts'
    const module = (await import(path)) as typeof import('../scripts/verify_authenticated_account')
    return module.verifyAuthenticatedAccount({ sync: true })
  })
}

test('assisted verifier restores preference, compares results and exports no identity or cookie', async ({
  page,
}) => {
  const state = await setup(page)
  const cookie = 'synthetic-session-secret'
  await page.context().addCookies([
    { name: 'gamerec_session', value: cookie, url: page.url(), httpOnly: true, sameSite: 'Lax' },
  ])
  const report = await verify(page)
  expect(report.sync_timestamp_persisted).toBe(true)
  expect(report.preference_checked).toBe(true)
  expect(report.preference_restored).toBe(true)
  expect(report.changed_scores_or_order).toBe(true)
  expect(report.restored_scores_and_order).toBe(true)
  expect(report.cross_account_sync_status).toBe(403)
  expect(report.cross_account_preference_status).toBe(403)
  expect(report.logged_out_sync_status).toBe(401)
  expect(report.logged_out_preference_status).toBe(401)
  expect(report.logged_out_library_status).toBe(401)
  expect(JSON.stringify(report)).not.toContain(owner)
  expect(JSON.stringify(report)).not.toContain(cookie)
  expect(state.writes).toEqual(['liked', 'disliked', 'clear'])
  expect(state.syncs).toBe(1)
})

test('a failed or uncertain write is still restored without retrying the write', async ({ page }) => {
  const state = await setup(page, 'write')
  const report = await verify(page)
  expect(report.preference_checked).toBe(false)
  expect(report.preference_error_status).toBe(503)
  expect(report.preference_restored).toBe(true)
  expect(report.restored_scores_and_order).toBe(true)
  expect(state.writes).toEqual(['liked', 'clear'])
})

test('failed cleanup retains the session and public app ID for recovery', async ({ page }) => {
  const state = await setup(page, 'cleanup')
  const report = await verify(page)
  expect(report.preference_restored).toBe(false)
  expect(report.recovery_app_id).toBe(10)
  expect(report.logout_status).toBeNull()
  expect(state.signedIn).toBe(true)
  expect(state.writes).toEqual(['liked', 'disliked', 'clear'])
})

test('anonymous verification stops before sync or preference work', async ({ page }) => {
  const state = await setup(page, 'anonymous')
  await expect(verify(page)).rejects.toThrow('401')
  expect(state.syncs).toBe(0)
  expect(state.writes).toEqual([])
})

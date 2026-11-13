import type { Page } from '@playwright/test'

/** Synthetic session response only; no application auth dependency is bypassed. */
export async function mockCurrentUser(page: Page, steamId: string | null) {
  const session = { steamId }
  await page.route((url) => url.pathname === '/api/auth/me', (route) =>
    session.steamId
      ? route.fulfill({ json: { steamid64: session.steamId } })
      : route.fulfill({ status: 401, json: { detail: { code: 'authentication_required' } } }),
  )
  return session
}

export async function changeCurrentUser(
  page: Page,
  session: { steamId: string | null },
  steamId: string | null,
) {
  session.steamId = steamId
  const refreshed = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/auth/me')
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange', { bubbles: true })))
  await refreshed
}

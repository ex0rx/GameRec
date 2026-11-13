/** Non-login HTTP readiness; real Steam credential entry remains manual. */
import { expect, test } from '@playwright/test'
import { writeFile } from 'node:fs/promises'

const origin = process.env.AUTH_TEST_PUBLIC_ORIGIN || 'http://localhost:5173'

test('real Vite proxy preserves backend auth, cookie and CORS decisions', async ({
  request,
}, info) => {
  const me = await request.get('/api/auth/me')
  const denied = await request.post('/api/auth/logout')
  const logout = await request.post('/api/auth/logout', { headers: { Origin: origin } })
  const login = await request.get('/api/auth/steam/login', { maxRedirects: 0 })
  const location = new URL(login.headers().location)
  const temporary = login.headers()['set-cookie'] || ''
  const invalid = await request.get('/api/auth/steam/callback?openid.sig=synthetic-proxy-check')
  const preflight = (from: string) =>
    request.fetch('/api/auth/logout', {
      method: 'OPTIONS',
      headers: { Origin: from, 'Access-Control-Request-Method': 'POST' },
    })
  const trusted = await preflight(origin)
  const untrusted = await preflight('https://untrusted.invalid')
  const localOtherOrigin = await request.get('/api/auth/me', {
    headers: { Origin: 'http://localhost:9000' },
  })
  const evidence = {
    unauthenticated_me: me.status(),
    logout_without_origin: denied.status(),
    trusted_logout: logout.status(),
    login_redirect: login.status(),
    steam_https:
      location.protocol === 'https:' &&
      location.host === 'steamcommunity.com' &&
      location.pathname === '/openid/login',
    configured_callback: (location.searchParams.get('openid.return_to') || '').startsWith(
      `${origin}/api/auth/steam/callback?login_id=`,
    ),
    cookie_http_only: temporary.includes('HttpOnly'),
    cookie_lax: temporary.includes('SameSite=lax'),
    cookie_root_host_only: temporary.includes('Path=/') && !temporary.includes('Domain='),
    malformed_callback: invalid.status(),
    trusted_preflight: trusted.status(),
    untrusted_preflight: untrusted.status(),
    explicit_credentials:
      trusted.headers()['access-control-allow-origin'] === origin &&
      trusted.headers()['access-control-allow-credentials'] === 'true',
    untrusted_origin_cannot_read: !localOtherOrigin.headers()['access-control-allow-origin'],
    private_headers: [me, denied, logout, login, invalid].every(
      (response) =>
        response.headers()['cache-control'] === 'no-store' &&
        response.headers()['referrer-policy'] === 'no-referrer',
    ),
    real_steam_login_performed: false,
  }
  expect(evidence).toEqual({
    unauthenticated_me: 401,
    logout_without_origin: 403,
    trusted_logout: 204,
    login_redirect: 303,
    steam_https: true,
    configured_callback: true,
    cookie_http_only: true,
    cookie_lax: true,
    cookie_root_host_only: true,
    malformed_callback: 400,
    trusted_preflight: 200,
    untrusted_preflight: 400,
    explicit_credentials: true,
    untrusted_origin_cannot_read: true,
    private_headers: true,
    real_steam_login_performed: false,
  })
  await writeFile(info.outputPath('readiness.json'), JSON.stringify(evidence, null, 2))
})

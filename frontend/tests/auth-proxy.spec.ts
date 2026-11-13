import { expect, test } from '@playwright/test'
import { redactAuthProxyError } from '../proxyPrivacy'

test('callback proxy errors redact queries and exception details before Vite logs them', () => {
  const error = new Error('synthetic assertion secret')
  const request = { url: '/auth/steam/callback?openid.sig=synthetic-secret&login_id=private' }
  const response = { req: { url: request.url } }
  redactAuthProxyError(error, request, response)
  expect(request.url).toBe('/auth/steam/callback?[redacted]')
  expect(response.req.url).toBe(request.url)
  expect(error.stack).not.toContain('synthetic')
  expect(error.message).toBe('Authentication proxy request failed')
})

test('ordinary proxy errors preserve existing diagnostics', () => {
  const error = new Error('ordinary failure')
  const request = { url: '/search/games?query=adventure' }
  redactAuthProxyError(error, request, {})
  expect(request.url).toBe('/search/games?query=adventure')
  expect(error.message).toBe('ordinary failure')
})

/** Do not let Vite's default proxy-error logger retain OpenID assertions. */
export function redactAuthProxyError(error: Error, request: { url?: string }, response: unknown) {
  if (!request.url?.split('?', 1)[0].replace(/\/$/, '').endsWith('/auth/steam/callback')) return
  request.url = request.url.split('?', 1)[0] + '?[redacted]'
  if (response && typeof response === 'object' && 'req' in response) {
    const outgoing = response.req
    if (outgoing && typeof outgoing === 'object' && 'url' in outgoing) outgoing.url = request.url
  }
  error.message = 'Authentication proxy request failed'
  error.stack = `${error.name}: Authentication proxy request failed`
}

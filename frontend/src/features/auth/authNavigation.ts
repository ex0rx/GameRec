/** Only a short-lived local destination is persisted; identity and tokens are not. */
export const LOGIN_DESTINATION_KEY = 'gamerec.auth.returnTo'
const LOGIN_DESTINATION_TTL = 10 * 60 * 1000
const destinations = ['/discover', '/library', '/recommendations'] as const
type LoginDestination = (typeof destinations)[number]

function destinationFor(pathname: string): LoginDestination {
  if (pathname === '/library' || /^\/library\/[^/]+\/?$/.test(pathname)) return '/library'
  return pathname === '/recommendations' ? '/recommendations' : '/discover'
}

export function rememberLoginDestination(pathname: string, now = Date.now()): void {
  try {
    window.sessionStorage.setItem(
      LOGIN_DESTINATION_KEY,
      JSON.stringify({ path: destinationFor(pathname), createdAt: now }),
    )
  } catch {
    // Login still works when browser storage is unavailable; Discover is the fallback.
  }
}

export function clearLoginDestination(): void {
  try {
    window.sessionStorage.removeItem(LOGIN_DESTINATION_KEY)
  } catch {
    // No session material is stored here and navigation must remain usable.
  }
}

export function consumeLoginDestination(now = Date.now()): LoginDestination | null {
  try {
    const raw = window.sessionStorage.getItem(LOGIN_DESTINATION_KEY)
    clearLoginDestination()
    if (!raw) return null
    const entry: unknown = JSON.parse(raw)
    if (
      !entry ||
      typeof entry !== 'object' ||
      !('path' in entry) ||
      !destinations.some((path) => path === entry.path) ||
      !('createdAt' in entry) ||
      typeof entry.createdAt !== 'number' ||
      !Number.isSafeInteger(entry.createdAt) ||
      entry.createdAt > now ||
      now - entry.createdAt > LOGIN_DESTINATION_TTL
    ) {
      return null
    }
    return entry.path as LoginDestination
  } catch {
    return null
  }
}

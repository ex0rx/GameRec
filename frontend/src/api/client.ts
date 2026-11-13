import type {
  GamePreference,
  GamePreferenceResponse,
  LibraryOptions,
  LibraryResponse,
  LibrarySyncResponse,
  RecommendationOptions,
  RecommendationRequest,
  RecommendationResponse,
  SearchExplanationRequest,
  SearchExplanationResponse,
  SearchGamesResponse,
} from './types'

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code?: string,
    public readonly retryAfter?: number,
  ) {
    super(`API request failed (${status})`)
    this.name = 'ApiError'
  }
}

const baseUrl = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')

async function request<T>(path: string, init: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${baseUrl}${path}`, init)
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0)
  }
  if (!response.ok) {
    let code: string | undefined
    try {
      const body: unknown = await response.json()
      if (body && typeof body === 'object' && 'detail' in body) {
        const detail = body.detail
        if (detail && typeof detail === 'object' && 'code' in detail) {
          if (typeof detail.code === 'string') code = detail.code
        }
      }
    } catch {
      // Status-based messages also cover non-JSON dependency failures.
    }
    const delay = response.headers.get('Retry-After')
    const seconds = delay && /^\d+$/.test(delay) ? Number(delay) : undefined
    throw new ApiError(
      response.status,
      code,
      seconds !== undefined && Number.isSafeInteger(seconds) ? seconds : undefined,
    )
  }
  if (response.status === 204) return undefined as T
  try {
    return (await response.json()) as T
  } catch {
    throw new ApiError(502)
  }
}

export function updateGamePreference(
  steamId: string,
  appId: number,
  preference: GamePreference | null,
): Promise<GamePreferenceResponse | void> {
  const path = `/users/${encodeURIComponent(steamId.trim())}/preferences/${appId}`
  return preference === null
    ? request<void>(path, { method: 'DELETE' })
    : request<GamePreferenceResponse>(path, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preference }),
      })
}

export function preferenceErrorMessage(error: unknown): string {
  const status = error instanceof ApiError ? error.status : 0
  if (status === 403) return 'Preference editing is not permitted for this account or environment.'
  if (status === 404) return 'This game or stored library is no longer available for rating.'
  if (status === 422) return 'The preference could not be saved. Reload the library and try again.'
  if (status === 503)
    return 'Preference storage is temporarily unavailable. Your previous choice is shown.'
  return 'Could not confirm this change. Your previous choice is shown; reload saved data before retrying.'
}

export function searchGames(params: URLSearchParams, signal: AbortSignal) {
  return request<SearchGamesResponse>(`/search/games?${params.toString()}`, { signal })
}

export function explainGame(body: SearchExplanationRequest, signal: AbortSignal) {
  return request<SearchExplanationResponse>('/explanations/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
}

export function getRecommendations(
  steamId: string,
  options: RecommendationOptions = {},
  signal?: AbortSignal,
): Promise<RecommendationResponse> {
  const body: RecommendationRequest = { steamid64: steamId.trim(), limit: options.limit ?? 20 }
  return request<RecommendationResponse>('/recommendations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
}

export function getSteamLibrary(
  steamId: string,
  options: LibraryOptions = {},
  signal?: AbortSignal,
): Promise<LibraryResponse> {
  const params = new URLSearchParams({
    limit: String(options.limit ?? 50),
    offset: String(options.offset ?? 0),
  })
  return request<LibraryResponse>(
    `/users/${encodeURIComponent(steamId.trim())}/library?${params.toString()}`,
    { signal },
  )
}

export async function syncSteamLibrary(
  steamId: string,
  signal?: AbortSignal,
): Promise<LibrarySyncResponse> {
  const timeout = AbortSignal.timeout(180_000)
  try {
    return await request<LibrarySyncResponse>(
      `/users/${encodeURIComponent(steamId.trim())}/library/sync`,
      { method: 'POST', signal: signal ? AbortSignal.any([signal, timeout]) : timeout },
    )
  } catch (error) {
    if (timeout.aborted && !signal?.aborted) throw new ApiError(504, 'sync_timeout')
    throw error
  }
}

export function librarySyncErrorMessage(error: unknown): string {
  const status = error instanceof ApiError ? error.status : 0
  const code = error instanceof ApiError ? error.code : undefined
  if (code === 'sync_forbidden' || status === 403)
    return 'Library sync is not permitted for this account or environment.'
  if (code === 'sync_in_progress' || status === 409)
    return 'A library sync is already running for this account. Refresh stored data before trying again.'
  if (code === 'sync_cooldown' || status === 429) {
    const delay = error instanceof ApiError ? error.retryAfter : undefined
    return delay === undefined
      ? 'Please wait before syncing again.'
      : `Please wait ${delay} seconds before syncing again.`
  }
  if (code === 'steam_unavailable')
    return 'Steam could not provide a visible library. Check the profile and game-details visibility before trying again.'
  if (code === 'steam_upstream_error' || status === 502)
    return 'Steam could not complete the library request. Please try again later.'
  if (status === 422) return 'Check the Steam ID before syncing again.'
  if (code === 'database_unavailable' || status === 503)
    return 'Library sync is temporarily unavailable. Refresh stored data before trying again.'
  if (code === 'sync_timeout' || status === 504)
    return 'The sync took too long. It may have saved data; refresh stored data before retrying.'
  return 'The connection was lost. The sync may have saved data; refresh stored data before retrying.'
}

export function errorMessage(
  error: unknown,
  kind: 'search' | 'explanation' | 'recommendation' | 'library',
): string {
  const status = error instanceof ApiError ? error.status : 0
  if (kind === 'library') {
    if (status === 404)
      return 'No previously imported library was found for this Steam ID. You can enter another ID above.'
    if (status === 422) return 'Check the Steam ID and library page, then try again.'
    if (status === 0) return 'Could not connect. Check your connection and try again.'
    return 'The stored library is temporarily unavailable. Please try again shortly.'
  }
  if (kind === 'recommendation') {
    if (status === 404) return 'No stored user was found for this account.'
    if (status === 422) return 'Check the Steam ID and result limit, then try again.'
    if (status === 0) return 'Could not connect. Check your connection and try again.'
    return 'Recommendations are temporarily unavailable. Please try again shortly.'
  }
  if (status === 422) return 'Check your query and filters, then try again.'
  if (kind === 'search') {
    return status === 0
      ? 'Could not connect. Check your connection and try again.'
      : 'Search is temporarily unavailable. Please try again shortly.'
  }
  if (status === 404) return 'This game is no longer available for an explanation.'
  if (status === 504) return 'The explanation took too long. You can try again.'
  if (status === 502) return 'The explanation could not be completed. Please try again.'
  return 'Explanations are temporarily unavailable. Your search results are still here.'
}

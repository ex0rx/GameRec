import type {
  LibraryOptions,
  LibraryResponse,
  RecommendationOptions,
  RecommendationRequest,
  RecommendationResponse,
  SearchExplanationRequest,
  SearchExplanationResponse,
  SearchGamesResponse,
} from './types'

export class ApiError extends Error {
  constructor(public readonly status: number) {
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
  if (!response.ok) throw new ApiError(response.status)
  try {
    return (await response.json()) as T
  } catch {
    throw new ApiError(502)
  }
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

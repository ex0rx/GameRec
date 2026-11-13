/** Wire contracts from GameRec's existing FastAPI schemas. */
export interface SearchGame {
  steam_app_id: number
  name: string
  similarity_score: number
  popularity_score: number
  review_quality: number
  hybrid_score: number
}

export interface SearchGamesResponse {
  query: string
  /** Number returned, not the number of catalogue matches. */
  total: number
  limit: number
  games: SearchGame[]
}

export interface SearchExplanationRequest {
  steam_app_id: number
  search_query: string
}

export interface SearchExplanationResponse {
  steam_app_id: number
  game_name: string
  explanation: string
  matching_features: string[]
}

export interface RecommendationOptions {
  limit?: number
}

export interface RecommendationRequest {
  steamid64: string
  limit: number
}

/** Existing RankedCandidate fields; descriptive metadata is not supplied. */
export interface RecommendedGame {
  steam_app_id: number
  name: string | null
  similarity_score: number
  popularity_score: number
  review_quality: number
  hybrid_score: number
  affinity_score: number
  recency_score: number
}

export interface RecommendationResponse {
  /** Number returned, not a catalogue-wide total. */
  total: number
  limit: number
  games: RecommendedGame[]
}

export interface LibraryOptions {
  limit?: number
  offset?: number
}

export interface LibraryGame {
  steam_app_id: number
  name: string
  playtime_forever_minutes: number | null
  playtime_2weeks_minutes: number | null
  preference: GamePreference | null
}

export type GamePreference = 'liked' | 'disliked' | 'neutral'

export interface GamePreferenceResponse {
  steamid64: string
  steam_app_id: number
  preference: GamePreference
  preference_last_updated_at: string
}

export interface LibraryResponse {
  steamid64: string
  total_games: number
  limit: number
  offset: number
  library_last_synced_at: string | null
  sync_allowed: boolean
  preference_writes_allowed: boolean
  games: LibraryGame[]
}

export interface LibrarySyncResponse {
  steamid64: string
  status: 'completed'
  processed_games: number
  total_games: number
  library_last_synced_at: string
  metadata_enrichment: 'complete' | 'partial'
}

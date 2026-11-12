import { USER_A } from './recommendation-fixtures'
import type { LibraryGame, LibraryResponse } from '../src/api/types'

// Synthetic stored-library responses; no Steam account or external API is used.
export const libraryGames: LibraryGame[] = [
  {
    steam_app_id: 40,
    name: 'Z — A very long library title with an adventure subtitle and room for another chapter',
    playtime_forever_minutes: 90,
    playtime_2weeks_minutes: 45,
  },
  {
    steam_app_id: 20,
    name: 'A quieter adventure',
    playtime_forever_minutes: 0,
    playtime_2weeks_minutes: 0,
  },
  {
    steam_app_id: 30,
    name: '   ',
    playtime_forever_minutes: null,
    playtime_2weeks_minutes: null,
  },
]

export function libraryResponse(
  games: LibraryGame[] = libraryGames,
  options: { steamId?: string; total?: number; offset?: number; syncedAt?: string | null } = {},
): LibraryResponse {
  return {
    steamid64: options.steamId ?? USER_A,
    total_games: options.total ?? games.length,
    limit: 50,
    offset: options.offset ?? 0,
    library_last_synced_at:
      options.syncedAt === undefined ? '2026-11-12T04:30:00Z' : options.syncedAt,
    games,
  }
}

export const pagedLibraryGames = Array.from({ length: 65 }, (_, index) => ({
  steam_app_id: 1000 + index,
  name: `Stored game ${index + 1}`,
  playtime_forever_minutes: 1200,
  playtime_2weeks_minutes: null,
}))

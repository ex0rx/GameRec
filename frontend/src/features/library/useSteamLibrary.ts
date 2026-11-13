import { useQuery } from '@tanstack/react-query'
import { getSteamLibrary } from '../../api/client'
import { LIBRARY_PAGE_SIZE, validLibrarySteamId } from './libraryState'

export function libraryQueryKey(
  steamId: string | null | undefined,
  limit = LIBRARY_PAGE_SIZE,
  offset = 0,
) {
  return ['steam-library', steamId?.trim() ?? '', { limit, offset }] as const
}

export function useSteamLibrary(
  steamId: string | null | undefined,
  limit = LIBRARY_PAGE_SIZE,
  offset = 0,
) {
  const queryKey = libraryQueryKey(steamId, limit, offset)
  return useQuery({
    queryKey,
    queryFn: ({ signal }) => getSteamLibrary(queryKey[1], queryKey[2], signal),
    enabled: validLibrarySteamId(queryKey[1]),
  })
}

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getSteamLibrary } from '../../api/client'
import { LIBRARY_PAGE_SIZE, validLibrarySteamId } from './libraryState'
import { privateRequest, readSessionGeneration } from '../auth/sessionCache'

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
  const client = useQueryClient()
  const queryKey = libraryQueryKey(steamId, limit, offset)
  return useQuery({
    queryKey,
    queryFn: ({ signal }) => privateRequest(client, queryKey[1], readSessionGeneration(client),
      () => getSteamLibrary(queryKey[1], queryKey[2], signal)),
    enabled: validLibrarySteamId(queryKey[1]),
  })
}

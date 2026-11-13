import { CancelledError, queryOptions, useIsMutating, useQuery, useQueryClient } from '@tanstack/react-query'
import { getCurrentUser } from '../../api/client'
import type { QueryClient } from '@tanstack/react-query'
import type { CurrentUser } from '../../api/types'
import { advanceSession, currentUserKey, logoutKey, readSessionGeneration } from './sessionCache'

export function currentUserOptions(client: QueryClient) {
  return queryOptions({
    queryKey: currentUserKey,
    queryFn: async ({ signal }) => {
      let generation = readSessionGeneration(client)
      const user = await getCurrentUser(signal)
      if (signal.aborted || generation !== readSessionGeneration(client)) throw new CancelledError()
      const previous = client.getQueryData<CurrentUser | null>(currentUserKey)
      if ((previous?.steamid64 ?? null) !== (user?.steamid64 ?? null)) {
        generation = advanceSession(client)
      }
      if (signal.aborted || generation !== readSessionGeneration(client)) throw new CancelledError()
      return user
    },
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
  })
}

export function useCurrentUser() {
  const client = useQueryClient()
  const signingOut = useIsMutating({ mutationKey: logoutKey }) > 0
  return useQuery({ ...currentUserOptions(client), enabled: !signingOut })
}

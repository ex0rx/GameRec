import { useMutation, useQueryClient } from '@tanstack/react-query'
import { logout } from '../../api/client'
import { advanceSession, clearPrivateCache, currentUserKey, logoutKey } from './sessionCache'
import { currentUserOptions } from './useCurrentUser'
import { clearLoginDestination } from './authNavigation'

export function useLogout() {
  const client = useQueryClient()
  return useMutation({
    mutationKey: logoutKey,
    mutationFn: logout,
    retry: false,
    onMutate: async () => {
      advanceSession(client, { clearPrivate: false })
      await client.cancelQueries({ queryKey: currentUserKey, exact: true })
    },
    onSuccess: () => {
      clearPrivateCache(client)
      client.setQueryData(currentUserKey, null)
      clearLoginDestination()
    },
    onError: async () => {
      // A lost response can still follow a successful server-side logout.
      try {
        await client.fetchQuery(currentUserOptions(client))
      } catch {
        // The auth query exposes uncertainty; never claim a confirmed logout.
      }
      void client.invalidateQueries({ predicate: (query) =>
        query.queryKey[0] === 'steam-library' || query.queryKey[0] === 'recommendations',
      })
    },
  })
}

import { useMutation, useMutationState, useQueryClient } from '@tanstack/react-query'
import { syncSteamLibrary } from '../../api/client'
import type { LibrarySyncResponse } from '../../api/types'
import { validLibrarySteamId } from './libraryState'

export function librarySyncMutationKey(steamId: string) {
  return ['library-sync', steamId.trim()] as const
}

export function useSyncSteamLibrary(steamId: string) {
  const id = steamId.trim()
  const queryClient = useQueryClient()
  const mutationKey = librarySyncMutationKey(id)
  const mutation = useMutation({
    mutationKey,
    mutationFn: (originalId: string) => syncSteamLibrary(originalId),
    retry: false,
    onSuccess: async (_result, originalId) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['steam-library', originalId] }),
        queryClient.invalidateQueries({ queryKey: ['recommendations', originalId] }),
      ])
    },
  })
  // Mutation-cache state survives leaving this page while a sync is still running.
  const states = useMutationState({
    filters: { mutationKey, exact: true },
    select: (entry) => ({
      status: entry.state.status,
      submittedAt: entry.state.submittedAt,
      data: entry.state.data as LibrarySyncResponse | undefined,
      error: entry.state.error,
    }),
  })
  const latest = states.reduce<(typeof states)[number] | undefined>(
    (previous, current) =>
      !previous || current.submittedAt >= previous.submittedAt ? current : previous,
    undefined,
  )
  const isPending = states.some((entry) => entry.status === 'pending')

  function sync() {
    if (!validLibrarySteamId(id)) return
    // Check the cache at click time too, so rapid clicks cannot start duplicate work.
    if (
      queryClient.getMutationCache().findAll({ mutationKey, exact: true, status: 'pending' }).length
    )
      return
    mutation.mutate(id)
  }

  return {
    sync,
    isPending,
    data: latest?.status === 'success' ? latest.data : undefined,
    error: latest?.status === 'error' ? latest.error : null,
  }
}

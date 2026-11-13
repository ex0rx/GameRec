import { useMutation, useMutationState, useQueryClient } from '@tanstack/react-query'
import { syncSteamLibrary } from '../../api/client'
import type { LibrarySyncResponse } from '../../api/types'
import { validLibrarySteamId } from './libraryState'
import { isCurrentSession, privateRequest, readSessionGeneration } from '../auth/sessionCache'

export function librarySyncMutationKey(steamId: string) {
  return ['library-sync', steamId.trim()] as const
}

export function useSyncSteamLibrary(steamId: string) {
  const id = steamId.trim()
  const queryClient = useQueryClient()
  const generation = readSessionGeneration(queryClient)
  const mutationKey = librarySyncMutationKey(id)
  const mutation = useMutation({
    mutationKey,
    mutationFn: (original: { steamId: string; generation: number }) => privateRequest(queryClient, original.steamId,
      original.generation, () => syncSteamLibrary(original.steamId)),
    onMutate: (original) => ({ generation: original.generation }),
    retry: false,
    onSuccess: async (_result, original, context) => {
      const originalId = original.steamId
      if (!isCurrentSession(queryClient, originalId, context.generation)) return
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
    if (!isCurrentSession(queryClient, id, generation)) return
    if (!validLibrarySteamId(id)) return
    // Check the cache at click time too, so rapid clicks cannot start duplicate work.
    if (
      queryClient.getMutationCache().findAll({ mutationKey, exact: true, status: 'pending' }).length
    )
      return
    mutation.mutate({ steamId: id, generation })
  }

  return {
    sync,
    isPending,
    data: latest?.status === 'success' ? latest.data : undefined,
    error: latest?.status === 'error' ? latest.error : null,
  }
}

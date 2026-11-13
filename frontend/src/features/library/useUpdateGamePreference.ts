import { useMutation, useMutationState, useQueryClient } from '@tanstack/react-query'
import { updateGamePreference } from '../../api/client'
import type { GamePreference, LibraryResponse } from '../../api/types'
import { validLibrarySteamId } from './libraryState'
import { isCurrentSession, privateRequest, readSessionGeneration } from '../auth/sessionCache'

interface PreferenceChange {
  steamId: string
  appId: number
  preference: GamePreference | null
  generation: number
}

export function preferenceMutationKey(steamId: string, appId: number) {
  return ['game-preference', steamId.trim(), appId] as const
}

export function useUpdateGamePreference(steamId: string, appId: number) {
  const id = steamId.trim()
  const client = useQueryClient()
  const generation = readSessionGeneration(client)
  const mutationKey = preferenceMutationKey(id, appId)
  const mutation = useMutation({
    mutationKey,
    mutationFn: (change: PreferenceChange) =>
      privateRequest(client, change.steamId, change.generation,
        () => updateGamePreference(change.steamId, change.appId, change.preference)),
    onMutate: (change) => ({ generation: change.generation }),
    retry: false,
    onSuccess: async (result, original, context) => {
      if (!isCurrentSession(client, original.steamId, context.generation)) return
      const libraryKey = ['steam-library', original.steamId]
      const recommendationKey = ['recommendations', original.steamId]
      // An earlier GET/recommendation response must not replace the confirmed write.
      await Promise.all([
        client.cancelQueries({ queryKey: libraryKey }),
        client.cancelQueries({ queryKey: recommendationKey }),
      ])
      if (!isCurrentSession(client, original.steamId, context.generation)) return
      const preference = result ? result.preference : null
      client.setQueriesData<LibraryResponse>({ queryKey: libraryKey }, (previous) =>
        previous
          ? {
              ...previous,
              games: previous.games.map((game) =>
                game.steam_app_id === original.appId ? { ...game, preference } : game,
              ),
            }
          : previous,
      )
      await Promise.all([
        client.invalidateQueries({ queryKey: libraryKey }),
        client.invalidateQueries({ queryKey: recommendationKey }),
      ])
    },
  })
  const states = useMutationState({
    filters: { mutationKey, exact: true },
    select: (entry) => ({
      status: entry.state.status,
      submittedAt: entry.state.submittedAt,
      error: entry.state.error,
      variables: entry.state.variables as PreferenceChange | undefined,
    }),
  })
  const latest = states.reduce<(typeof states)[number] | undefined>(
    (previous, current) =>
      !previous || current.submittedAt >= previous.submittedAt ? current : previous,
    undefined,
  )
  const isPending = states.some((entry) => entry.status === 'pending')

  function save(preference: GamePreference | null) {
    if (!isCurrentSession(client, id, generation)) return
    if (!validLibrarySteamId(id) || !Number.isInteger(appId) || appId <= 0) return
    if (client.getMutationCache().findAll({ mutationKey, exact: true, status: 'pending' }).length)
      return
    mutation.mutate({ steamId: id, appId, preference, generation })
  }

  return {
    save,
    retry: () => {
      if (latest?.status === 'error' && latest.variables) save(latest.variables.preference)
    },
    isPending,
    isSaved: latest?.status === 'success',
    error: latest?.status === 'error' ? latest.error : null,
    reload: () => client.refetchQueries({ queryKey: ['steam-library', id], type: 'active' }),
  }
}

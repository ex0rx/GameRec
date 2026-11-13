/** Test-only async session boundary; never imported by the application entry point. */
import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { createQueryClient } from '../src/app/queryClient'
import type { CurrentUser } from '../src/api/types'
import {
  advanceSession,
  currentUserKey,
  expireSession,
  readSessionGeneration,
} from '../src/features/auth/sessionCache'
import { useSteamLibrary } from '../src/features/library/useSteamLibrary'
import { useUpdateGamePreference } from '../src/features/library/useUpdateGamePreference'
import { useRecommendations } from '../src/features/recommendations/useRecommendations'
import { USER_A } from './recommendation-fixtures'

const client = createQueryClient()
client.setQueryData(currentUserKey, { steamid64: USER_A })
client.setQueryData(['search-games', 'public'], { query: 'public discovery remains cached' })
let changeIdentity: ((identity: string | null) => void) | null = null

export function changeHarnessSession(identity: string | null) {
  advanceSession(client, { clearPrivate: true })
  client.setQueryData(currentUserKey, identity ? { steamid64: identity } : null)
  changeIdentity?.(identity)
}

export function expireHarnessSession() {
  const current = client.getQueryData<CurrentUser | null>(currentUserKey)
  if (current) expireSession(client, current.steamid64, readSessionGeneration(client))
  changeIdentity?.(null)
}

export function readCacheState() {
  return {
    identity: client.getQueryData<CurrentUser | null>(currentUserKey)?.steamid64 ?? null,
    privateData: client.getQueryCache().getAll()
      .filter((query) => ['steam-library', 'recommendations'].includes(String(query.queryKey[0])))
      .filter((query) => query.state.data !== undefined)
      .map((query) => ({ key: query.queryKey, data: query.state.data })),
    privateMutations: client.getMutationCache().getAll()
      .filter((mutation) => ['game-preference', 'library-sync'].includes(String(mutation.options.mutationKey?.[0])))
      .length,
    publicData: client.getQueryData(['search-games', 'public']),
  }
}

function Harness() {
  const [identity, setIdentity] = useState<string | null>(USER_A)
  useEffect(() => {
    changeIdentity = setIdentity
    return () => { changeIdentity = null }
  }, [])
  const library = useSteamLibrary(identity)
  const recommendations = useRecommendations(identity)
  const preference = useUpdateGamePreference(identity ?? '', 40)
  return (
    <>
      <h1>Session isolation test harness</h1>
      <output data-testid="library-name">{library.data?.games[0]?.name ?? ''}</output>
      <output data-testid="recommendation-name">{recommendations.data?.games[0]?.name ?? ''}</output>
      <output data-testid="preference">{library.data?.games[0]?.preference ?? 'unrated'}</output>
      <output data-testid="mutation-state">{preference.isPending ? 'pending' : 'idle'}</output>
      <button onClick={() => preference.save('liked')} disabled={preference.isPending}>Like test game</button>
    </>
  )
}

if (import.meta.env.DEV)
  createRoot(document.getElementById('harness')!).render(
    <QueryClientProvider client={client}><Harness /></QueryClientProvider>,
  )

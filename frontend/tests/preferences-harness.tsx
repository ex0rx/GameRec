/** Test-only entry; never part of the production application bundle. */
import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { createQueryClient } from '../src/app/queryClient'
import { getRecommendations } from '../src/api/client'
import type { LibraryResponse } from '../src/api/types'
import { useSteamLibrary } from '../src/features/library/useSteamLibrary'
import { useUpdateGamePreference } from '../src/features/library/useUpdateGamePreference'
import { libraryResponse } from './library-fixtures'
import { game, USER_A, USER_B } from './recommendation-fixtures'

const client = createQueryClient()
const keys = [
  ['steam-library', USER_A, { limit: 50, offset: 0 }],
  ['steam-library', USER_A, { limit: 50, offset: 50 }],
  ['recommendations', USER_A, { limit: 20 }],
  ['recommendations', USER_A, { limit: 50 }],
  ['steam-library', USER_B, { limit: 50, offset: 0 }],
  ['recommendations', USER_B, { limit: 20 }],
]
for (const key of keys)
  client.setQueryData(
    key,
    key[0] === 'steam-library'
      ? libraryResponse(undefined, { steamId: key[1] as string })
      : { total: 1, limit: 20, games: [{ ...game, name: 'Baseline' }] },
  )

function Harness() {
  const library = useSteamLibrary(USER_A, 50, 0)
  const update = useUpdateGamePreference(` ${USER_A} `, 40)
  const [snapshot, setSnapshot] = useState<unknown>(null)
  const [oldRequest, setOldRequest] = useState('idle')
  return (
    <>
      <output data-testid="preference">{library.data?.games[0].preference ?? 'unrated'}</output>
      <output data-testid="mutation">
        {update.isPending ? 'pending' : update.isSaved ? 'saved' : 'idle'}
      </output>
      <output data-testid="old-request">{oldRequest}</output>
      <button onClick={() => update.save('liked')}>Like test game</button>
      <button onClick={() => void library.refetch()}>Start old library read</button>
      <button
        onClick={() => {
          setOldRequest('pending')
          void client
            .fetchQuery({
              queryKey: keys[2],
              staleTime: 0,
              queryFn: ({ signal }) => getRecommendations(USER_A, {}, signal),
            })
            .then((response) => setOldRequest(response.games[0].name ?? 'Unnamed'))
            .catch(() => setOldRequest('cancelled'))
        }}
      >
        Start old recommendations
      </button>
      <button
        onClick={() =>
          setSnapshot({
            stale: keys.map((key) => client.getQueryState(key)?.isInvalidated),
            preferences: [keys[0], keys[1], keys[4]].map(
              (key) => client.getQueryData<LibraryResponse>(key)?.games[0].preference ?? null,
            ),
            recommendationName: (client.getQueryData(keys[2]) as { games: { name: string }[] })
              .games[0].name,
          })
        }
      >
        Read cache state
      </button>
      <output data-testid="cache">{JSON.stringify(snapshot)}</output>
    </>
  )
}

if (import.meta.env.DEV)
  createRoot(document.getElementById('harness')!).render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  )

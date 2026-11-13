/** Test-only entry; it is not part of the application build or routing. */
import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { createQueryClient } from '../src/app/queryClient'
import { useSteamLibrary } from '../src/features/library/useSteamLibrary'
import { useSyncSteamLibrary } from '../src/features/library/useSyncSteamLibrary'
import { libraryResponse } from './library-fixtures'
import { USER_A, USER_B } from './recommendation-fixtures'

const client = createQueryClient()
const keys = [
  ['steam-library', USER_A, { limit: 50, offset: 0 }],
  ['steam-library', USER_A, { limit: 50, offset: 50 }],
  ['recommendations', USER_A, { limit: 20 }],
  ['recommendations', USER_A, { limit: 50 }],
  ['steam-library', USER_B, { limit: 50, offset: 0 }],
  ['recommendations', USER_B, { limit: 20 }],
]
for (const key of keys) client.setQueryData(key, libraryResponse())

function Harness() {
  const query = useSteamLibrary(USER_A, 50, 0)
  const sync = useSyncSteamLibrary(` ${USER_A} `)
  const [snapshot, setSnapshot] = useState<unknown>(null)
  return (
    <>
      <output data-testid="sync-state">
        {sync.isPending ? 'pending' : sync.data ? 'completed' : 'idle'}
      </output>
      <output data-testid="library-count">{query.data?.games.length}</output>
      <button onClick={sync.sync}>Sync test library</button>
      <button
        onClick={() => setSnapshot(keys.map((key) => client.getQueryState(key)?.isInvalidated))}
      >
        Read cache state
      </button>
      <output data-testid="cache-state">{JSON.stringify(snapshot)}</output>
    </>
  )
}

if (import.meta.env.DEV)
  createRoot(document.getElementById('harness')!).render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  )

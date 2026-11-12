/** Test-only Vite entry, never imported by the production application. */
import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { createQueryClient } from '../src/app/queryClient'
import { useRecommendations } from '../src/features/recommendations/useRecommendations'
import { errorMessage } from '../src/api/client'

let applyInputs: ((inputs: { steamId: string; limit: number }) => void) | null = null

/** Keep the live ID in memory, out of the DOM, URLs and failure snapshots. */
export function applyRecommendationTestInputs(steamId: string, limit = 20) {
  if (!applyInputs) throw new Error('Recommendation test harness is not ready')
  applyInputs({ steamId, limit })
}

function Harness() {
  const [draftId, setDraftId] = useState('')
  const [draftLimit, setDraftLimit] = useState('20')
  const [applied, setApplied] = useState({ steamId: '', limit: 20 })
  useEffect(() => {
    applyInputs = setApplied
    return () => {
      applyInputs = null
    }
  }, [])
  const query = useRecommendations(applied.steamId, { limit: applied.limit })
  const state = !applied.steamId.trim() ? 'disabled' : query.isFetching ? 'loading' : query.status
  return (
    <>
      <h1>Recommendation integration test harness</h1>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          setApplied({ steamId: draftId, limit: Number(draftLimit) })
        }}
      >
        <label>
          Test Steam ID
          <input
            type="password"
            autoComplete="off"
            value={draftId}
            onChange={(event) => setDraftId(event.target.value)}
          />
        </label>
        <label>
          Test limit
          <input
            type="number"
            value={draftLimit}
            onChange={(event) => setDraftLimit(event.target.value)}
          />
        </label>
        <button type="submit">Apply test inputs</button>
      </form>
      <output data-testid="state">{state}</output>
      <output data-testid="count">{query.data?.total ?? ''}</output>
      {query.error && <p role="alert">{errorMessage(query.error, 'recommendation')}</p>}
      <ul>
        {query.data?.games.map((game) => (
          <li key={game.steam_app_id}>{game.name ?? `Steam app ${game.steam_app_id}`}</li>
        ))}
      </ul>
    </>
  )
}

if (import.meta.env.DEV) {
  createRoot(document.getElementById('harness')!).render(
    <QueryClientProvider client={createQueryClient()}>
      <Harness />
    </QueryClientProvider>,
  )
}

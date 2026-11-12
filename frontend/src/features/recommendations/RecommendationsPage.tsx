import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Library, RefreshCw } from 'lucide-react'
import type { RecommendedGame } from '../../api/types'
import { errorMessage } from '../../api/client'
import { GameCard } from '../../components/GameCard'
import { GameDetailsDrawer } from '../../components/GameDetailsDrawer'
import { ErrorState, ResultSkeleton } from '../../components/States'
import { DevelopmentProfile } from './DevelopmentProfile'
import { useRecommendations } from './useRecommendations'
import { validLibrarySteamId } from '../library/libraryState'

export function RecommendationsPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const [steamId, setSteamId] = useState<string | null>(() => {
    const state: unknown = location.state
    return typeof state === 'object' &&
      state !== null &&
      'steamId' in state &&
      typeof state.steamId === 'string' &&
      validLibrarySteamId(state.steamId)
      ? state.steamId.trim()
      : null
  })
  useEffect(() => {
    // Consume the explicit Library handoff once; identity remains page-local.
    if (location.state != null)
      void navigate(
        { pathname: location.pathname, search: location.search, hash: location.hash },
        { replace: true, state: null },
      )
  }, [location, navigate])
  const [selection, setSelection] = useState<{
    game: RecommendedGame
    trigger: HTMLElement
  } | null>(null)
  const query = useRecommendations(steamId)

  return (
    <>
      <header className="page-header">
        <div className="breadcrumb">
          GameRec <span>/</span> Recommendations
        </div>
        <span className="header-caption">A new place to start.</span>
      </header>
      <div className="discovery-content recommendations-content">
        <div className="page-heading">
          <span className="small-label">Recommendations</span>
          <h1>Recommended for you</h1>
          <p>Suggestions informed by a Steam library stored in GameRec.</p>
        </div>
        {import.meta.env.DEV && (
          <DevelopmentProfile
            selected={!!steamId}
            onSelect={(value) => {
              setSelection(null)
              setSteamId(value)
            }}
            onClear={() => {
              setSelection(null)
              setSteamId(null)
            }}
          />
        )}
        {!steamId ? (
          <section className="feedback-state profile-state" aria-labelledby="profile-heading">
            <Library size={28} aria-hidden="true" />
            <h2 id="profile-heading">No profile selected</h2>
            <p>
              Recommendations need an existing library stored in GameRec.{' '}
              {import.meta.env.DEV
                ? 'Select a development profile above to see suggestions.'
                : 'Account selection is not available in this version.'}
            </p>
          </section>
        ) : (
          <section
            className="results-section"
            aria-labelledby="recommendations-heading"
            aria-busy={query.isFetching}
          >
            <div className="section-heading results-heading recommendations-heading">
              <div>
                <h2 id="recommendations-heading">Your suggestions</h2>
                {query.data && !query.isFetching && !query.isError && (
                  <span role="status">
                    {query.data.games.length} {query.data.games.length === 1 ? 'game' : 'games'}{' '}
                    returned
                  </span>
                )}
              </div>
              <button
                className="refresh-button"
                type="button"
                disabled={query.isFetching}
                onClick={() => {
                  setSelection(null)
                  void query.refetch()
                }}
              >
                <RefreshCw size={14} aria-hidden="true" /> Refresh
              </button>
            </div>
            {query.isFetching || query.isPending ? (
              <ResultSkeleton label="Loading recommendations" />
            ) : query.isError ? (
              <ErrorState
                title="Recommendations couldn’t be loaded"
                message={errorMessage(query.error, 'recommendation')}
                onRetry={() => {
                  void query.refetch()
                }}
              />
            ) : query.data?.games.length === 0 ? (
              <div className="feedback-state" role="status">
                <Library size={28} aria-hidden="true" />
                <h2>No recommendations available yet</h2>
                <p>
                  This profile may not have enough usable stored game data, or no candidates met the
                  recommendation requirements. This does not mean the Steam library is empty.
                </p>
              </div>
            ) : (
              query.data && (
                <>
                  <p className="recommendation-context">
                    Informed by stored library ownership, playtime and preferences where available.
                  </p>
                  <div className="game-grid">
                    {query.data.games.map((game, index) => (
                      <GameCard
                        key={game.steam_app_id}
                        game={game}
                        position={index + 1}
                        onInspect={(trigger) => setSelection({ game, trigger })}
                      />
                    ))}
                  </div>
                  <p className="catalogue-note">
                    Suggestions follow GameRec’s recommendation order and the currently indexed
                    catalogue.
                  </p>
                </>
              )
            )}
          </section>
        )}
        <footer className="discovery-footer">
          <span>GameRec</span>
          <span>Find something worth playing.</span>
        </footer>
      </div>
      {selection && (
        <GameDetailsDrawer
          game={selection.game}
          trigger={selection.trigger}
          onClose={() => setSelection(null)}
        />
      )}
    </>
  )
}

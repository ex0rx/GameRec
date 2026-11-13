import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { Library, RefreshCw } from 'lucide-react'
import type { CurrentUser, RecommendedGame } from '../../api/types'
import { errorMessage } from '../../api/client'
import { GameCard } from '../../components/GameCard'
import { GameDetailsDrawer } from '../../components/GameDetailsDrawer'
import { ErrorState, ResultSkeleton } from '../../components/States'
import { useRecommendations } from './useRecommendations'

export function RecommendationsPage() {
  const { steamid64: steamId } = useOutletContext<CurrentUser>()
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
          <p>Suggestions informed by your stored Steam library and preferences.</p>
        </div>
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
                Your library may not have enough usable stored game data, or no candidates met the
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

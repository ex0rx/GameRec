import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { errorMessage, searchGames } from '../../api/client'
import type { SearchGame } from '../../api/types'
import { EmptyState, ErrorState, ResultSkeleton } from '../../components/States'
import { SearchForm } from './SearchForm'
import { GameCard } from '../../components/GameCard'
import { ExplanationDrawer } from './ExplanationDrawer'
import { readSearchState, searchParams, validateSearch } from './searchState'
import type { SearchState } from './searchState'

interface Selection {
  game: SearchGame
  query: string
  explain: boolean
  trigger: HTMLElement
}

export function DiscoveryPage() {
  const [params, setParams] = useSearchParams()
  const state = readSearchState(params)
  const submitted = params.has('query')
  const validation = submitted ? validateSearch(state) : null
  const requestParams = searchParams(state)
  const [selection, setSelection] = useState<Selection | null>(null)
  const results = useQuery({
    queryKey: ['search', requestParams.toString()],
    queryFn: ({ signal }) => searchGames(requestParams, signal),
    enabled: submitted && validation === null,
  })

  function submit(next: SearchState) {
    const nextParams = searchParams(next)
    setSelection(null)
    if (nextParams.toString() === requestParams.toString() && submitted) {
      void results.refetch()
    } else {
      setParams(nextParams)
    }
  }

  const activeSelection =
    selection?.query === state.query.trim() && !results.isFetching ? selection : null

  return (
    <>
      <header className="page-header" data-has-search={submitted}>
        <div className="breadcrumb">
          GameRec <span>/</span> Discover
        </div>
        <span className="header-caption">Games, in your own words.</span>
      </header>
      <div className={`discovery-content${submitted ? ' has-search' : ''}`}>
        <div className="page-heading">
          <span className="small-label">Discover</span>
          <h1>Search games.</h1>
          <p>Less scrolling. More of what you’re looking for.</p>
        </div>
        <SearchForm
          key={params.toString()}
          initialState={state}
          initialError={validation}
          onSearch={submit}
          busy={results.isFetching}
        />
        {submitted && !validation && (
          <section
            className="results-section"
            aria-labelledby="results-heading"
            aria-busy={results.isFetching}
          >
            <div className="section-heading results-heading">
              <div>
                <h2 id="results-heading">Search results</h2>
                {results.data && !results.isFetching && (
                  <span role="status">
                    {results.data.games.length} {results.data.games.length === 1 ? 'game' : 'games'}{' '}
                    returned
                  </span>
                )}
              </div>
              <span>Ordered for your search</span>
            </div>
            {results.isFetching || results.isPending ? (
              <ResultSkeleton />
            ) : results.isError ? (
              <ErrorState
                message={errorMessage(results.error, 'search')}
                onRetry={() => {
                  void results.refetch()
                }}
              />
            ) : results.data && results.data.games.length === 0 ? (
              <EmptyState />
            ) : (
              results.data && (
                <>
                  <div className="game-grid">
                    {results.data.games.map((game, index) => (
                      <GameCard
                        key={game.steam_app_id}
                        game={game}
                        position={index + 1}
                        onInspect={(trigger) =>
                          setSelection({
                            game,
                            query: state.query.trim(),
                            explain: false,
                            trigger,
                          })
                        }
                        onExplain={(trigger) =>
                          setSelection({
                            game,
                            query: state.query.trim(),
                            explain: true,
                            trigger,
                          })
                        }
                      />
                    ))}
                  </div>
                  <p className="catalogue-note">
                    Results come from the currently indexed Steam catalogue. Not every Steam game is
                    indexed.
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
      {activeSelection && (
        <ExplanationDrawer
          key={`${params.toString()}:${activeSelection.game.steam_app_id}`}
          game={activeSelection.game}
          query={activeSelection.query}
          explainImmediately={activeSelection.explain}
          trigger={activeSelection.trigger}
          onClose={() => setSelection(null)}
        />
      )}
    </>
  )
}

import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, Library } from 'lucide-react'
import { ApiError, errorMessage } from '../../api/client'
import type { LibraryGame, LibraryResponse } from '../../api/types'
import { ErrorState } from '../../components/States'
import { GameDetailsDrawer } from '../../components/GameDetailsDrawer'
import { LibraryForm } from './LibraryForm'
import { LibraryGameRow } from './LibraryGameRow'
import { LibrarySyncAction } from './LibrarySyncAction'
import { useSteamLibrary } from './useSteamLibrary'
import {
  LIBRARY_PAGE_SIZE,
  libraryPath,
  readLibraryPage,
  validLibrarySteamId,
} from './libraryState'

function LibrarySkeleton() {
  return (
    <div role="status" aria-label="Loading library">
      <span className="sr-only">Loading library…</span>
      <div aria-hidden="true">
        {Array.from({ length: 6 }, (_, index) => (
          <div className="library-game-row" key={index}>
            <div className="artwork skeleton" />
            <div className="library-game-info">
              <div className="skeleton-line skeleton" />
              <div className="skeleton-short skeleton" />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function LibraryGames({ data }: { data: LibraryResponse }) {
  const [selection, setSelection] = useState<{ game: LibraryGame; trigger: HTMLElement } | null>(
    null,
  )
  return (
    <>
      <div className="library-games">
        {data.games.map((game) => (
          <LibraryGameRow
            key={game.steam_app_id}
            game={game}
            steamId={data.steamid64}
            preferencesAllowed={data.preference_writes_allowed}
            onInspect={(trigger) => setSelection({ game, trigger })}
          />
        ))}
      </div>
      {selection && (
        <GameDetailsDrawer
          game={selection.game}
          trigger={selection.trigger}
          description="A game recorded in this stored library. Visit Steam for descriptions, features and reviews."
          onClose={() => setSelection(null)}
        />
      )}
    </>
  )
}

const syncFormat = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  timeZone: 'UTC',
  timeZoneName: 'short',
})

export function LibraryPage() {
  const { steamid64 = '' } = useParams()
  const steamId = steamid64.trim()
  const [params] = useSearchParams()
  const page = readLibraryPage(params)
  const navigate = useNavigate()
  const valid = validLibrarySteamId(steamId)
  const query = useSteamLibrary(steamId, LIBRARY_PAGE_SIZE, (page - 1) * LIBRARY_PAGE_SIZE)
  const data = query.data
  const unknown = query.error instanceof ApiError && query.error.status === 404

  return (
    <>
      <header className="page-header" data-has-library={valid}>
        <div className="breadcrumb">
          GameRec <span>/</span> Library
        </div>
        <span className="header-caption">Games and recorded playtime.</span>
      </header>
      <div className="discovery-content library-content" data-has-library={valid}>
        <div className="page-heading">
          <span className="small-label">Library</span>
          <h1>Game Library</h1>
          <p>Explore recorded games and playtime from a stored Steam library.</p>
        </div>
        <LibraryForm
          key={steamid64}
          initialId={steamid64}
          onSubmit={(id) => {
            const path = libraryPath(id)
            if (id === steamId && page === 1 && valid) void query.refetch({ cancelRefetch: false })
            else void navigate(path)
          }}
        />
        {!valid ? (
          <section className="feedback-state" aria-labelledby="library-start-heading">
            <Library size={28} aria-hidden="true" />
            <h2 id="library-start-heading">Choose a stored library</h2>
            <p>
              Enter a Steam ID64 above to browse previously imported games. No Steam account
              connection is required.
            </p>
          </section>
        ) : (
          <section
            className="library-results"
            aria-labelledby="library-heading"
            aria-busy={query.isFetching}
          >
            <div className="section-heading library-results-heading">
              <div>
                <h2 id="library-heading">Stored games</h2>
                <p className="library-identity" data-private-steam-id>
                  Viewing library {steamId}
                </p>
              </div>
              {data && (
                <Link className="button secondary" to="/recommendations" state={{ steamId }}>
                  View recommendations <ArrowRight size={16} aria-hidden="true" />
                </Link>
              )}
            </div>
            {!data && (query.isFetching || query.isPending) ? (
              <LibrarySkeleton />
            ) : !data && query.isError ? (
              <ErrorState
                title={unknown ? 'No stored library found' : 'Library couldn’t be loaded'}
                message={errorMessage(query.error, 'library')}
                onRetry={() => {
                  void query.refetch()
                }}
              />
            ) : (
              data && (
                <>
                  {import.meta.env.DEV && data.sync_allowed && (
                    <LibrarySyncAction
                      key={steamId}
                      steamId={steamId}
                      isRefreshing={query.isFetching}
                      onRefresh={() => {
                        void query.refetch({ cancelRefetch: false })
                      }}
                    />
                  )}
                  {query.isError && (
                    <div className="library-refresh-warning" role="alert">
                      <p>
                        The displayed snapshot could not be refreshed. Refresh stored data before
                        making more changes.
                      </p>
                      <button
                        className="button secondary"
                        disabled={query.isFetching}
                        onClick={() => {
                          void query.refetch({ cancelRefetch: false })
                        }}
                      >
                        Refresh stored library
                      </button>
                    </div>
                  )}
                  {query.isFetching && (
                    <p className="library-recorded-note" role="status">
                      Refreshing stored library…
                    </p>
                  )}
                  <div className="library-overview">
                    <span>
                      {data.total_games} {data.total_games === 1 ? 'stored game' : 'stored games'}
                    </span>
                    {data.library_last_synced_at ? (
                      <span>
                        Last synced{' '}
                        <time dateTime={data.library_last_synced_at}>
                          {syncFormat.format(new Date(data.library_last_synced_at))}
                        </time>
                      </span>
                    ) : (
                      <span>Sync time unavailable</span>
                    )}
                  </div>
                  <p className="library-recorded-note">Playtime reflects the last stored sync.</p>
                  {data.total_games === 0 ? (
                    <div className="feedback-state" role="status">
                      <Library size={28} aria-hidden="true" />
                      <h2>No games stored for this library</h2>
                      <p>
                        GameRec has no recorded games for this profile. This does not establish that
                        the Steam library is empty.
                      </p>
                    </div>
                  ) : data.games.length === 0 ? (
                    <div className="feedback-state" role="status">
                      <h2>No games on this page</h2>
                      <p>
                        The stored library has {data.total_games} games. Choose another page to
                        browse them.
                      </p>
                      <Link className="button secondary" to={libraryPath(steamId)}>
                        Return to first page
                      </Link>
                    </div>
                  ) : (
                    <LibraryGames key={`${steamId}:${page}`} data={data} />
                  )}
                  <nav className="library-pagination" aria-label="Library pages">
                    <span role="status">
                      {data.games.length
                        ? `${data.offset + 1}–${data.offset + data.games.length} of ${data.total_games} games`
                        : `0 games shown · ${data.total_games} stored`}
                    </span>
                    <div>
                      <button
                        className="button secondary"
                        disabled={data.offset === 0}
                        onClick={() => {
                          void navigate(
                            libraryPath(steamId, Math.max(1, Math.floor(data.offset / data.limit))),
                          )
                        }}
                      >
                        <ArrowLeft size={14} aria-hidden="true" /> Previous
                      </button>
                      <button
                        className="button secondary"
                        disabled={data.offset + data.limit >= data.total_games}
                        onClick={() => {
                          void navigate(
                            libraryPath(steamId, Math.floor(data.offset / data.limit) + 2),
                          )
                        }}
                      >
                        Next <ArrowRight size={14} aria-hidden="true" />
                      </button>
                    </div>
                  </nav>
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
    </>
  )
}

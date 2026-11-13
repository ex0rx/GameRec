import { ArrowUpRight } from 'lucide-react'
import { Artwork } from '../../components/Artwork'
import type { LibraryGame } from '../../api/types'
import { formatPlaytime } from './playtime'
import { GamePreferenceControls } from './GamePreferenceControls'
import { preferenceLabel } from './preference'

export function LibraryGameRow({
  game,
  steamId,
  preferencesAllowed,
  onInspect,
}: {
  game: LibraryGame
  steamId: string
  preferencesAllowed: boolean
  onInspect: (trigger: HTMLElement) => void
}) {
  const title = game.name.trim() || `Steam app ${game.steam_app_id}`
  return (
    <article className="library-game-row" data-steam-app-id={game.steam_app_id}>
      <button
        className="game-cover-button"
        aria-label={`Inspect ${title}`}
        onClick={(event) => onInspect(event.currentTarget)}
      >
        <Artwork appId={game.steam_app_id} name={title} />
      </button>
      <div className="library-game-info">
        <h3>
          <button onClick={(event) => onInspect(event.currentTarget)}>{title}</button>
        </h3>
        <dl className="library-playtime">
          <div>
            <dt>Lifetime</dt>
            <dd>{formatPlaytime(game.playtime_forever_minutes)}</dd>
          </div>
          {game.playtime_2weeks_minutes != null && (
            <div>
              <dt>Past 2 weeks</dt>
              <dd>{formatPlaytime(game.playtime_2weeks_minutes)}</dd>
            </div>
          )}
        </dl>
        {import.meta.env.DEV && preferencesAllowed ? (
          <GamePreferenceControls
            key={`${steamId}:${game.steam_app_id}`}
            steamId={steamId}
            appId={game.steam_app_id}
            title={title}
            preference={game.preference ?? null}
          />
        ) : (
          <p className="game-preference-readonly">{preferenceLabel(game.preference ?? null)}</p>
        )}
      </div>
      <a
        className="library-steam-link"
        href={`https://store.steampowered.com/app/${game.steam_app_id}/`}
        target="_blank"
        rel="noreferrer"
        aria-label={`View ${title} on Steam`}
      >
        Steam <ArrowUpRight size={16} aria-hidden="true" />
      </a>
    </article>
  )
}

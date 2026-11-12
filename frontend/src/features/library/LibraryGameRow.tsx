import { ArrowUpRight } from 'lucide-react'
import { Artwork } from '../../components/Artwork'
import type { LibraryGame } from '../../api/types'
import { formatPlaytime } from './playtime'

export function LibraryGameRow({
  game,
  onInspect,
}: {
  game: LibraryGame
  onInspect: (trigger: HTMLElement) => void
}) {
  const title = game.name.trim() || `Steam app ${game.steam_app_id}`
  return (
    <article className="library-game-row">
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

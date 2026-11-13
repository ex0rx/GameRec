import { ArrowUpRight, MessageSquareText } from 'lucide-react'
import { Artwork } from './Artwork'

export interface CardGame {
  steam_app_id: number
  name: string | null
}

interface Props {
  game: CardGame
  position: number
  onInspect: (trigger: HTMLElement) => void
  onExplain?: (trigger: HTMLElement) => void
}

export function GameCard({ game, position, onInspect, onExplain }: Props) {
  const title = game.name?.trim() || `Steam app ${game.steam_app_id}`
  return (
    <article className="game-card">
      <button
        className="game-cover-button"
        onClick={(event) => onInspect(event.currentTarget)}
        aria-label={`Inspect ${title}`}
      >
        <Artwork appId={game.steam_app_id} name={title} />
        <span className="cover-inspect" aria-hidden="true">
          Inspect game <ArrowUpRight size={16} />
        </span>
      </button>
      <div className="game-info">
        <div className="game-title-row">
          <span className="result-position">{String(position).padStart(2, '0')}</span>
          <h3>
            <button onClick={(event) => onInspect(event.currentTarget)}>{title}</button>
          </h3>
        </div>
        <div className="game-actions">
          {onExplain ? (
            <button className="explain-link" onClick={(event) => onExplain(event.currentTarget)}>
              <MessageSquareText size={14} aria-hidden="true" /> Why this game?
            </button>
          ) : (
            <button className="explain-link" onClick={(event) => onInspect(event.currentTarget)}>
              View details
            </button>
          )}
          <a
            href={`https://store.steampowered.com/app/${game.steam_app_id}/`}
            target="_blank"
            rel="noreferrer"
            aria-label={`View ${title} on Steam`}
          >
            <ArrowUpRight size={17} aria-hidden="true" />
          </a>
        </div>
      </div>
    </article>
  )
}

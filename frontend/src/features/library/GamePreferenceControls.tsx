import { useRef } from 'react'
import { ThumbsDown, ThumbsUp } from 'lucide-react'
import type { GamePreference } from '../../api/types'
import { preferenceErrorMessage } from '../../api/client'
import { useUpdateGamePreference } from './useUpdateGamePreference'
import { preferenceLabel } from './preference'

export function GamePreferenceControls({
  steamId,
  appId,
  title,
  preference,
}: {
  steamId: string
  appId: number
  title: string
  preference: GamePreference | null
}) {
  const update = useUpdateGamePreference(steamId, appId)
  const likeButton = useRef<HTMLButtonElement>(null)

  return (
    <div className="game-preference">
      <div className="game-preference-actions" role="group" aria-label={`Preference for ${title}`}>
        <button
          ref={likeButton}
          type="button"
          aria-label={`Like ${title}`}
          aria-pressed={preference === 'liked'}
          aria-disabled={update.isPending}
          title={preference === 'liked' ? 'Remove like' : 'Like this game'}
          onClick={() => update.save(preference === 'liked' ? null : 'liked')}
        >
          <ThumbsUp size={14} aria-hidden="true" /> Like
        </button>
        <button
          type="button"
          aria-label={`Dislike ${title}`}
          aria-pressed={preference === 'disliked'}
          aria-disabled={update.isPending}
          title={preference === 'disliked' ? 'Remove dislike' : 'Dislike this game'}
          onClick={() => update.save(preference === 'disliked' ? null : 'disliked')}
        >
          <ThumbsDown size={14} aria-hidden="true" /> Dislike
        </button>
        <span className="game-preference-state">{preferenceLabel(preference)}</span>
        {preference === 'neutral' && (
          <button
            type="button"
            aria-label={`Clear legacy neutral preference for ${title}`}
            aria-disabled={update.isPending}
            onClick={() => {
              update.save(null)
              likeButton.current?.focus()
            }}
          >
            Clear
          </button>
        )}
        {update.isPending && <span role="status">Saving…</span>}
      </div>
      {update.isSaved && !update.isPending && (
        <p className="preference-saved" role="status">
          Preference saved. Recommendations will update when opened or refreshed.
        </p>
      )}
      {update.error && (
        <div className="preference-error">
          <p role="alert">{preferenceErrorMessage(update.error)}</p>
          <button type="button" onClick={() => void update.reload()}>
            Reload saved preference
          </button>
          <button type="button" onClick={update.retry}>
            Retry preference
          </button>
        </div>
      )}
    </div>
  )
}

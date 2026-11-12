import { useEffect, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import * as Dialog from '@radix-ui/react-dialog'
import { ArrowUpRight, CircleAlert, MessageSquareText, X } from 'lucide-react'
import { explainGame, errorMessage } from '../../api/client'
import type { SearchGame } from '../../api/types'
import { Artwork } from '../../components/Artwork'

interface Props {
  game: SearchGame
  query: string
  explainImmediately: boolean
  trigger: HTMLElement
  onClose: () => void
}

/** Remounted for each selection/query, keeping responses tied to their inputs. */
export function ExplanationDrawer({ game, query, explainImmediately, trigger, onClose }: Props) {
  const controller = useRef<AbortController | null>(null)
  const started = useRef(false)
  const [requested, setRequested] = useState(false)
  const explanation = useMutation({
    mutationFn: () => {
      controller.current?.abort()
      controller.current = new AbortController()
      return explainGame(
        { steam_app_id: game.steam_app_id, search_query: query },
        controller.current.signal,
      )
    },
  })
  const { mutate } = explanation

  useEffect(() => {
    // Cancel before starting during StrictMode's development-only effect replay.
    const timer = window.setTimeout(() => {
      if (explainImmediately && !started.current) {
        started.current = true
        mutate()
      }
    }, 0)
    return () => {
      window.clearTimeout(timer)
      controller.current?.abort()
    }
  }, [explainImmediately, mutate])

  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="drawer-overlay" />
        <Dialog.Content
          className="explanation-drawer"
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            trigger.focus()
          }}
        >
          <div className="drawer-top">
            <span className="small-label">A closer look</span>
            <Dialog.Close className="icon-button" aria-label="Close game details">
              <X size={20} aria-hidden="true" />
            </Dialog.Close>
          </div>
          <Artwork appId={game.steam_app_id} name={game.name} />
          <div className="drawer-body">
            <span className="small-label">Steam catalogue / {game.steam_app_id}</span>
            <Dialog.Title>{game.name}</Dialog.Title>
            <Dialog.Description className="drawer-description">
              Explore this result in the context of your search.
            </Dialog.Description>
            <a
              className="button secondary steam-button"
              href={`https://store.steampowered.com/app/${game.steam_app_id}/`}
              target="_blank"
              rel="noreferrer"
            >
              View on Steam <ArrowUpRight size={16} aria-hidden="true" />
            </a>
            <section className="explanation-section" aria-labelledby="explanation-heading">
              <h2 id="explanation-heading">
                <MessageSquareText size={18} aria-hidden="true" /> Why this game?
              </h2>
              <blockquote>{query}</blockquote>
              {explanation.isPending ? (
                <div className="explanation-loading" role="status">
                  <span className="loading-dot" aria-hidden="true" />
                  <span>
                    Putting the match into words…<small>This can take a moment.</small>
                  </span>
                </div>
              ) : explanation.isError ? (
                <div className="explanation-error" role="alert">
                  <CircleAlert size={19} aria-hidden="true" />
                  <p>{errorMessage(explanation.error, 'explanation')}</p>
                  <button className="button secondary" onClick={() => mutate()}>
                    Try again
                  </button>
                </div>
              ) : explanation.data ? (
                <div className="explanation-output" aria-live="polite">
                  <p>{explanation.data.explanation}</p>
                  {explanation.data.matching_features.length > 0 && (
                    <>
                      <h3>Matching features</h3>
                      <ul>
                        {explanation.data.matching_features.map((feature, index) => (
                          <li key={`${index}-${feature}`}>{feature}</li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              ) : (
                <div className="explanation-intro">
                  <p>Get a short explanation of how this game relates to your search.</p>
                  <button
                    className="button primary"
                    disabled={requested}
                    onClick={() => {
                      setRequested(true)
                      mutate()
                    }}
                  >
                    Explain this match <ArrowUpRight size={16} aria-hidden="true" />
                  </button>
                </div>
              )}
              <p className="explanation-caption">
                AI-generated explanations can be imperfect. Check the game’s Steam page for details.
              </p>
            </section>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

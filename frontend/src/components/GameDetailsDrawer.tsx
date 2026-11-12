import * as Dialog from '@radix-ui/react-dialog'
import { ArrowUpRight, X } from 'lucide-react'
import type { CardGame } from './GameCard'
import { Artwork } from './Artwork'

export function GameDetailsDrawer({
  game,
  trigger,
  onClose,
  description = 'A game from this recommendation set. Visit Steam for descriptions, features and reviews.',
}: {
  game: CardGame
  trigger: HTMLElement
  onClose: () => void
  description?: string
}) {
  const title = game.name?.trim() || `Steam app ${game.steam_app_id}`
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
          <Artwork appId={game.steam_app_id} name={title} />
          <div className="drawer-body">
            <span className="small-label">Steam catalogue / {game.steam_app_id}</span>
            <Dialog.Title>{title}</Dialog.Title>
            <Dialog.Description className="drawer-description">{description}</Dialog.Description>
            <a
              className="button secondary steam-button"
              href={`https://store.steampowered.com/app/${game.steam_app_id}/`}
              target="_blank"
              rel="noreferrer"
            >
              View on Steam <ArrowUpRight size={16} aria-hidden="true" />
            </a>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

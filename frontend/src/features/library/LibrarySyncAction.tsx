import { RefreshCw } from 'lucide-react'
import { librarySyncErrorMessage } from '../../api/client'
import { useSyncSteamLibrary } from './useSyncSteamLibrary'

export function LibrarySyncAction({
  steamId,
  onRefresh,
  isRefreshing,
}: {
  steamId: string
  onRefresh: () => void
  isRefreshing: boolean
}) {
  const sync = useSyncSteamLibrary(steamId)
  return (
    <div className="library-sync">
      <button className="button secondary" onClick={sync.sync} disabled={sync.isPending}>
        <RefreshCw size={14} aria-hidden="true" />
        {sync.isPending ? 'Syncing…' : 'Sync Library'}
      </button>
      <p className="library-sync-note">Refresh recorded games and playtime from Steam.</p>
      {sync.isPending && (
        <p role="status">Syncing with Steam. Your stored library stays available below.</p>
      )}
      {sync.data && (
        <div role="status">
          <p>
            Library synced. {sync.data.processed_games} games processed; {sync.data.total_games}{' '}
            games stored.
          </p>
          {sync.data.metadata_enrichment === 'partial' && (
            <p>Some game metadata is still pending. Your library and playtime were saved.</p>
          )}
        </div>
      )}
      {sync.error && (
        <>
          <p role="alert">{librarySyncErrorMessage(sync.error)}</p>
          <button className="button secondary" onClick={onRefresh} disabled={isRefreshing}>
            Refresh stored library
          </button>
        </>
      )}
    </div>
  )
}

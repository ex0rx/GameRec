import { useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { validLibrarySteamId } from './libraryState'

export function LibraryForm({
  initialId,
  onSubmit,
}: {
  initialId: string
  onSubmit: (steamId: string) => void
}) {
  const [draft, setDraft] = useState(initialId)
  const [error, setError] = useState<string | null>(
    initialId && !validLibrarySteamId(initialId)
      ? 'Enter a Steam ID64 containing exactly 17 digits.'
      : null,
  )
  return (
    <form
      className="library-form"
      noValidate
      onSubmit={(event) => {
        event.preventDefault()
        if (!validLibrarySteamId(draft)) {
          setError('Enter a Steam ID64 containing exactly 17 digits.')
          return
        }
        setError(null)
        onSubmit(draft.trim())
      }}
    >
      <label htmlFor="library-steam-id">Steam ID64</label>
      <div className="profile-input-row">
        <input
          id="library-steam-id"
          data-private-steam-id
          type="text"
          inputMode="numeric"
          autoComplete="off"
          spellCheck={false}
          maxLength={64}
          placeholder="Enter a 17-digit Steam ID"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          aria-invalid={!!error}
          aria-describedby={error ? 'library-id-error' : 'library-id-help'}
        />
        <button className="button primary" type="submit">
          Load library <ArrowRight size={16} aria-hidden="true" />
        </button>
      </div>
      {error && (
        <p id="library-id-error" className="form-error" role="alert">
          {error}
        </p>
      )}
      <p id="library-id-help" className="profile-help">
        Browse a library already stored in GameRec. This does not sign you in, verify ownership or
        sync Steam data.
      </p>
    </form>
  )
}

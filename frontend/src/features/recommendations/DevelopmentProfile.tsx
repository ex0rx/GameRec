import { useState } from 'react'
import { ArrowRight, ChevronDown } from 'lucide-react'
import { validDevelopmentSteamId } from './developmentIdentity'

interface Props {
  selected: boolean
  onSelect: (steamId: string) => void
  onClear: () => void
}

export function DevelopmentProfile({ selected, onSelect, onClear }: Props) {
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  return (
    <details className="development-profile" open={!selected || undefined}>
      <summary>
        Development profile <span className="development-badge">Local only</span>
        <ChevronDown size={14} aria-hidden="true" />
      </summary>
      <div className="development-profile-body">
        <p>
          Select an existing stored profile for testing. This does not sign you in or verify account
          ownership.
        </p>
        {selected && (
          <div className="selected-profile">
            <span>Development profile selected</span>
            <button
              className="text-button"
              type="button"
              onClick={() => {
                setDraft('')
                setError(null)
                onClear()
              }}
            >
              Clear profile
            </button>
          </div>
        )}
        <form
          onSubmit={(event) => {
            event.preventDefault()
            if (!validDevelopmentSteamId(draft)) {
              setError(
                'Enter a positive unsigned 64-bit Steam ID using digits, without leading zeros.',
              )
              return
            }
            onSelect(draft.trim())
            setDraft('')
            setError(null)
          }}
          noValidate
        >
          <label htmlFor="development-steam-id">Development Steam ID</label>
          <div className="profile-input-row">
            <input
              id="development-steam-id"
              type="password"
              inputMode="numeric"
              autoComplete="off"
              spellCheck={false}
              maxLength={64}
              placeholder="Steam ID64"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              aria-invalid={!!error}
              aria-describedby={error ? 'profile-error' : 'profile-help'}
            />
            <button className="button primary" type="submit">
              Load recommendations <ArrowRight size={16} aria-hidden="true" />
            </button>
          </div>
          {error && (
            <p className="form-error" id="profile-error" role="alert">
              {error}
            </p>
          )}
          <p className="profile-help" id="profile-help">
            Use a profile already stored in GameRec. The ID is not saved to browser storage.
            Selection clears when you leave this page.
          </p>
        </form>
      </div>
    </details>
  )
}

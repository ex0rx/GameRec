import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowRight, ChevronDown, Search, SlidersHorizontal } from 'lucide-react'
import { examples, searchParams, validateSearch } from './searchState'
import type { SearchState } from './searchState'

interface Props {
  initialState: SearchState
  onSearch: (state: SearchState) => void
  busy: boolean
  initialError: string | null
}

export function SearchForm({ initialState, onSearch, busy, initialError }: Props) {
  const [draft, setDraft] = useState(initialState)
  const [error, setError] = useState(initialError)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const filterCount = [
    draft.genres,
    draft.categories,
    draft.release_year_from,
    draft.release_year_to,
    draft.min_reviews,
  ].filter(Boolean).length
  const update = (key: keyof SearchState, value: string) =>
    setDraft((previous) => ({ ...previous, [key]: value }))

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const invalid = validateSearch(draft)
    setError(invalid)
    if (!invalid) onSearch(draft)
  }

  return (
    <>
      <form className="search-form" onSubmit={submit} noValidate>
        <label htmlFor="game-query" className="search-label">
          What would you like to play?
        </label>
        <div className={`search-box${error ? ' has-error' : ''}`}>
          <Search className="search-icon" size={21} aria-hidden="true" />
          <textarea
            ref={inputRef}
            id="game-query"
            name="query"
            rows={2}
            value={draft.query}
            maxLength={500}
            placeholder="A co-op game with a world to explore…"
            aria-invalid={!!error}
            aria-describedby={error ? 'search-error' : 'query-help'}
            onChange={(event) => update('query', event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault()
                event.currentTarget.form?.requestSubmit()
              }
            }}
          />
          <button
            className="button primary search-submit"
            type="submit"
            disabled={
              busy && searchParams(draft).toString() === searchParams(initialState).toString()
            }
          >
            Search <ArrowRight size={17} aria-hidden="true" />
          </button>
        </div>
        <div className="search-hint" id="query-help">
          <span>Describe a setting, a mechanic, or a feeling.</span>
          <span>{draft.query.length}/500</span>
        </div>
        {error && (
          <p id="search-error" role="alert" className="form-error">
            {error}
          </p>
        )}
        <details className="filters" open={filterCount > 0 || undefined}>
          <summary>
            <SlidersHorizontal size={15} aria-hidden="true" /> Filters{' '}
            {filterCount > 0 && <span className="filter-count">{filterCount}</span>}
            <ChevronDown size={14} className="filter-chevron" aria-hidden="true" />
          </summary>
          <div className="filters-content">
            <div className="filter-fields">
              <label>
                Genres
                <input
                  aria-label="Genres"
                  aria-describedby="genres-help"
                  value={draft.genres}
                  onChange={(event) => update('genres', event.target.value)}
                  placeholder="Action, RPG"
                />
                <span id="genres-help">Separate genre names with commas.</span>
              </label>
              <label>
                Categories
                <input
                  aria-label="Categories"
                  aria-describedby="categories-help"
                  value={draft.categories}
                  onChange={(event) => update('categories', event.target.value)}
                  placeholder="Co-op, Single-player"
                />
                <span id="categories-help">Use Steam feature names, separated by commas.</span>
              </label>
              <label>
                Released from
                <input
                  type="number"
                  min="1"
                  max="9999"
                  step="1"
                  value={draft.release_year_from}
                  placeholder="Any year"
                  onChange={(event) => update('release_year_from', event.target.value)}
                />
              </label>
              <label>
                Released through
                <input
                  type="number"
                  min="1"
                  max="9999"
                  step="1"
                  value={draft.release_year_to}
                  placeholder="Any year"
                  onChange={(event) => update('release_year_to', event.target.value)}
                />
              </label>
              <label>
                Minimum reviews
                <input
                  type="number"
                  min="0"
                  step="1"
                  value={draft.min_reviews}
                  placeholder="100"
                  onChange={(event) => update('min_reviews', event.target.value)}
                />
              </label>
              <label>
                Result limit
                <select
                  value={draft.limit}
                  onChange={(event) => update('limit', event.target.value)}
                >
                  <option value="20">20 games</option>
                  <option value="50">50 games</option>
                  <option value="100">100 games</option>
                </select>
              </label>
            </div>
            <p className="filter-note">
              Any selected genre and any selected category can match; other filters must also match.
              Release years include both bounds. Search requires at least 100 reviews even if you
              enter a lower minimum.
            </p>
            {filterCount > 0 && (
              <button
                type="button"
                className="text-button"
                onClick={() =>
                  setDraft((previous) => ({
                    ...previous,
                    genres: '',
                    categories: '',
                    release_year_from: '',
                    release_year_to: '',
                    min_reviews: '',
                  }))
                }
              >
                Clear filters
              </button>
            )}
          </div>
        </details>
      </form>
      {!initialState.query && (
        <section className="search-start" aria-labelledby="try-heading">
          <div className="section-heading">
            <h2 id="try-heading">Try a search</h2>
            <span>Make it your own</span>
          </div>
          <div className="example-list">
            {examples.map((example, index) => (
              <button
                className="example-query"
                key={example.query}
                onClick={() => {
                  update('query', example.query)
                  setError(null)
                  inputRef.current?.focus()
                }}
              >
                <span className="example-number">0{index + 1}</span>
                <span className="example-copy">
                  <strong>{example.title}</strong>
                  <span>{example.query}</span>
                  <small>{example.label}</small>
                </span>
                <ArrowRight size={18} aria-hidden="true" />
              </button>
            ))}
          </div>
          <p className="catalogue-note">
            Search the indexed Steam catalogue. Find a game, then ask why it fits.
          </p>
        </section>
      )}
    </>
  )
}

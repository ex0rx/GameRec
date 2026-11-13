import { ArrowRight, CircleAlert, SearchX } from 'lucide-react'

export function ErrorState({
  message,
  onRetry,
  title = 'Something interrupted the search',
}: {
  message: string
  onRetry: () => void
  title?: string
}) {
  return (
    <div className="feedback-state error-state" role="alert">
      <CircleAlert size={24} aria-hidden="true" />
      <h2>{title}</h2>
      <p>{message}</p>
      <button className="button secondary" onClick={onRetry}>
        Try again <ArrowRight size={16} aria-hidden="true" />
      </button>
    </div>
  )
}

export function EmptyState() {
  return (
    <div className="feedback-state" role="status">
      <SearchX size={28} aria-hidden="true" />
      <h2>No games found for this search</h2>
      <p>
        Try a broader description or remove a filter. Search covers the currently indexed Steam
        catalogue.
      </p>
    </div>
  )
}

export function ResultSkeleton({ label = 'Searching games' }: { label?: string }) {
  return (
    <div role="status" aria-label={label} className="results-loading">
      <span className="sr-only">{label}…</span>
      <div className="game-grid" aria-hidden="true">
        {Array.from({ length: 6 }, (_, index) => (
          <div className="game-card skeleton-card" key={index}>
            <div className="artwork skeleton" />
            <div className="skeleton-line skeleton" />
            <div className="skeleton-short skeleton" />
          </div>
        ))}
      </div>
    </div>
  )
}

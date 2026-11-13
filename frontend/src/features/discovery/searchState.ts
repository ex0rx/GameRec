export interface SearchState {
  query: string
  genres: string
  categories: string
  release_year_from: string
  release_year_to: string
  min_reviews: string
  limit: string
}

export const examples = [
  {
    title: 'A world to get lost in',
    query: 'Open-world RPG with deep character progression and a strong story',
    label: 'Exploration · story · progression',
  },
  {
    title: 'Better with a friend',
    query: 'Cooperative survival crafting game with challenging bosses',
    label: 'Co-op · crafting · a challenge',
  },
  {
    title: 'Straight into the action',
    query: 'Fast-paced first-person shooter with intense combat',
    label: 'First-person · fast-paced · combat',
  },
]

export function readSearchState(params: URLSearchParams): SearchState {
  return {
    query: params.get('query') || '',
    genres: params.getAll('genres').join(', '),
    categories: params.getAll('categories').join(', '),
    release_year_from: params.get('release_year_from') || '',
    release_year_to: params.get('release_year_to') || '',
    min_reviews: params.get('min_reviews') || '',
    limit: params.get('limit') || '20',
  }
}

export function searchParams(state: SearchState): URLSearchParams {
  const params = new URLSearchParams({ query: state.query.trim(), limit: state.limit })
  for (const key of ['genres', 'categories'] as const) {
    for (const label of state[key]
      .split(',')
      .map((value) => value.trim())
      .filter(Boolean)) {
      params.append(key, label)
    }
  }
  for (const key of ['release_year_from', 'release_year_to', 'min_reviews'] as const) {
    if (state[key]) params.set(key, state[key])
  }
  return params
}

export function validateSearch(state: SearchState): string | null {
  if (!state.query.trim()) return 'Describe what you would like to play.'
  if (state.query.trim().length > 500) return 'Keep your search to 500 characters or fewer.'
  if (!['20', '50', '100'].includes(state.limit)) return 'Choose 20, 50 or 100 results.'
  for (const key of ['release_year_from', 'release_year_to'] as const) {
    const value = state[key]
    if (value && (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 9999)) {
      return 'Release years must be whole numbers between 1 and 9999.'
    }
  }
  if (
    state.release_year_from &&
    state.release_year_to &&
    Number(state.release_year_from) > Number(state.release_year_to)
  ) {
    return 'The earliest release year must not be later than the latest year.'
  }
  if (
    state.min_reviews &&
    (!/^\d+$/.test(state.min_reviews) || !Number.isSafeInteger(Number(state.min_reviews)))
  ) {
    return 'Minimum reviews must be a non-negative whole number.'
  }
  return null
}

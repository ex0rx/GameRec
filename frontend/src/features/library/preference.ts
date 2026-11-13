import type { GamePreference } from '../../api/types'

export function preferenceLabel(preference: GamePreference | null) {
  if (preference === 'liked') return 'Liked'
  if (preference === 'disliked') return 'Disliked'
  if (preference === 'neutral') return 'Neutral (legacy)'
  return 'Unrated'
}

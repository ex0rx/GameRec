import { useQuery } from '@tanstack/react-query'
import { getRecommendations } from '../../api/client'
import type { RecommendationOptions } from '../../api/types'

export function recommendationQueryKey(
  steamId: string | null | undefined,
  options: RecommendationOptions = {},
) {
  return ['recommendations', steamId?.trim() ?? '', { limit: options.limit ?? 20 }] as const
}

export function useRecommendations(
  steamId: string | null | undefined,
  options: RecommendationOptions = {},
) {
  const queryKey = recommendationQueryKey(steamId, options)
  return useQuery({
    queryKey,
    queryFn: ({ signal }) => getRecommendations(queryKey[1], queryKey[2], signal),
    enabled: queryKey[1].length > 0,
  })
}

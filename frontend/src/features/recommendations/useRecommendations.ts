import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getRecommendations } from '../../api/client'
import type { RecommendationOptions } from '../../api/types'
import { privateRequest, readSessionGeneration } from '../auth/sessionCache'

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
  const client = useQueryClient()
  const queryKey = recommendationQueryKey(steamId, options)
  return useQuery({
    queryKey,
    queryFn: ({ signal }) => privateRequest(client, queryKey[1], readSessionGeneration(client),
      () => getRecommendations(queryKey[1], queryKey[2], signal)),
    enabled: queryKey[1].length > 0,
  })
}

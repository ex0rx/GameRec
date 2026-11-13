import { CancelledError } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'
import { ApiError } from '../../api/client'
import type { CurrentUser } from '../../api/types'

export const currentUserKey = ['current-user'] as const
export const logoutKey = ['auth-logout'] as const
// A request fence only; identity remains exclusively in the current-user query.
const generations = new WeakMap<QueryClient, number>()
const queryRoots = new Set(['steam-library', 'recommendations'])
const mutationRoots = new Set(['game-preference', 'library-sync'])

export function readSessionGeneration(client: QueryClient) {
  return generations.get(client) ?? 0
}

export function clearPrivateCache(client: QueryClient) {
  client.removeQueries({ predicate: (query) => queryRoots.has(String(query.queryKey[0])) })
  const cache = client.getMutationCache()
  for (const mutation of cache.getAll()) {
    if (mutationRoots.has(String(mutation.options.mutationKey?.[0]))) cache.remove(mutation)
  }
}

export function advanceSession(
  client: QueryClient,
  options: { clearPrivate?: boolean } = {},
) {
  const generation = readSessionGeneration(client) + 1
  generations.set(client, generation)
  void client.cancelQueries({ predicate: (query) => queryRoots.has(String(query.queryKey[0])) })
  if (options.clearPrivate !== false) clearPrivateCache(client)
  return generation
}

export function isCurrentSession(client: QueryClient, identity: string, generation: number) {
  return readSessionGeneration(client) === generation &&
    client.isMutating({ mutationKey: logoutKey }) === 0 &&
    client.getQueryState(currentUserKey)?.status === 'success' &&
    client.getQueryData<CurrentUser | null>(currentUserKey)?.steamid64 === identity
}

export function expireSession(client: QueryClient, identity: string, generation: number) {
  if (!isCurrentSession(client, identity, generation)) return
  advanceSession(client)
  void client.cancelQueries({ queryKey: currentUserKey, exact: true })
  client.setQueryData(currentUserKey, null)
}

export async function privateRequest<T>(
  client: QueryClient,
  identity: string,
  generation: number,
  operation: () => Promise<T>,
): Promise<T> {
  if (!isCurrentSession(client, identity, generation)) throw new CancelledError()
  try {
    const value = await operation()
    if (!isCurrentSession(client, identity, generation)) throw new CancelledError()
    return value
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) expireSession(client, identity, generation)
    throw error
  }
}

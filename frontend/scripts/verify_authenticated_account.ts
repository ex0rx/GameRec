/** Explicit browser-only verification; never exports an identity or session cookie. */
import {
  ApiError,
  getRecommendations,
  getSteamLibrary,
  syncSteamLibrary,
  updateGamePreference,
} from '../src/api/client'
import type { RecommendationResponse } from '../src/api/types'

const base = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')

async function status(path: string, method: string, body?: object) {
  return fetch(`${base}${path}`, {
    method,
    credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
}

function errorStatus(error: unknown) {
  return error instanceof ApiError ? error.status : 0
}

function compare(before: RecommendationResponse, after: RecommendationResponse) {
  const original = new Map(
    before.games.map((game, index) => [game.steam_app_id, { game, rank: index + 1 }]),
  )
  return after.games.map((game, index) => {
    const previous = original.get(game.steam_app_id)
    return {
      steam_app_id: game.steam_app_id,
      before_rank: previous?.rank ?? null,
      after_rank: index + 1,
      before_hybrid_score: previous?.game.hybrid_score ?? null,
      after_hybrid_score: game.hybrid_score,
      before_affinity_score: previous?.game.affinity_score ?? null,
      after_affinity_score: game.affinity_score,
    }
  })
}

export async function verifyAuthenticatedAccount(options: { sync: boolean }) {
  const me = await status('/auth/me', 'GET')
  if (me.status !== 200) throw new ApiError(me.status)
  const identity: unknown = await me.json()
  if (
    !identity ||
    typeof identity !== 'object' ||
    !('steamid64' in identity) ||
    typeof identity.steamid64 !== 'string' ||
    !/^[0-9]{17}$/.test(identity.steamid64)
  ) {
    throw new ApiError(502)
  }
  const id = identity.steamid64
  const before = await getSteamLibrary(id, { limit: 100 })
  const sync = options.sync ? await syncSteamLibrary(id) : null
  const library = sync ? await getSteamLibrary(id, { limit: 100 }) : before
  const report = {
    authenticated_me_status: me.status,
    own_library_status: 200,
    stored_games: library.total_games,
    sync_performed: sync !== null,
    sync_completed: sync?.status === 'completed',
    sync_timestamp_persisted: sync
      ? library.library_last_synced_at === sync.library_last_synced_at
      : null,
    sync_timestamp_changed: sync
      ? before.library_last_synced_at !== library.library_last_synced_at
      : null,
    metadata_enrichment: sync?.metadata_enrichment ?? null,
    preference_checked: false,
    preference_restored: false,
    recovery_app_id: null as number | null,
    preference_error_status: null as number | null,
    recommendations_baseline: null as RecommendationResponse | null,
    recommendations_liked: null as RecommendationResponse | null,
    recommendations_disliked: null as RecommendationResponse | null,
    recommendations_restored: null as RecommendationResponse | null,
    changed_scores_or_order: null as boolean | null,
    restored_scores_and_order: null as boolean | null,
    comparison: [] as ReturnType<typeof compare>,
    cross_account_sync_status: null as number | null,
    cross_account_preference_status: null as number | null,
    cross_account_library_status: null as number | null,
    cross_account_recommendations_status: null as number | null,
    logout_status: null as number | null,
    logged_out_sync_status: null as number | null,
    logged_out_preference_status: null as number | null,
    logged_out_library_status: null as number | null,
  }
  // Only an originally unrated row is modified; cleanup restores absence, even
  // when a write's response is lost. Selection is limited to this first page.
  const game = library.games
    .filter((entry) => entry.preference === null)
    .sort((a, b) => (b.playtime_forever_minutes ?? 0) - (a.playtime_forever_minutes ?? 0))[0]
  if (game) {
    report.recommendations_baseline = await getRecommendations(id)
    try {
      await updateGamePreference(id, game.steam_app_id, 'liked')
      const liked = await getSteamLibrary(id, { limit: 100 })
      if (
        liked.games.find((entry) => entry.steam_app_id === game.steam_app_id)?.preference !==
        'liked'
      ) {
        throw new ApiError(502)
      }
      report.recommendations_liked = await getRecommendations(id)
      await updateGamePreference(id, game.steam_app_id, 'disliked')
      const disliked = await getSteamLibrary(id, { limit: 100 })
      if (
        disliked.games.find((entry) => entry.steam_app_id === game.steam_app_id)?.preference !==
        'disliked'
      ) {
        throw new ApiError(502)
      }
      report.recommendations_disliked = await getRecommendations(id)
      report.preference_checked = true
      report.comparison = compare(
        report.recommendations_baseline,
        report.recommendations_disliked,
      )
      report.changed_scores_or_order =
        JSON.stringify(report.recommendations_baseline.games) !==
        JSON.stringify(report.recommendations_disliked.games)
    } catch (error) {
      report.preference_error_status = errorStatus(error)
    } finally {
      try {
        await updateGamePreference(id, game.steam_app_id, null)
        const restored = await getSteamLibrary(id, { limit: 100 })
        report.preference_restored =
          restored.games.find((entry) => entry.steam_app_id === game.steam_app_id)
            ?.preference === null
      } catch {
        report.preference_restored = false
      }
      if (!report.preference_restored) report.recovery_app_id = game.steam_app_id
    }
    // Keep the session available for recovery if cleanup could not be confirmed.
    if (!report.preference_restored) return report
    report.recommendations_restored = await getRecommendations(id)
    report.restored_scores_and_order =
      JSON.stringify(report.recommendations_baseline.games) ===
      JSON.stringify(report.recommendations_restored.games)
  }
  const other = id === '1'.repeat(17) ? '2'.repeat(17) : '1'.repeat(17)
  report.cross_account_sync_status = (
    await status(`/users/${other}/library/sync`, 'POST')
  ).status
  report.cross_account_preference_status = (
    await status(`/users/${other}/preferences/${game?.steam_app_id ?? 1}`, 'PUT', {
      preference: 'liked',
    })
  ).status
  report.cross_account_library_status = (
    await status(`/users/${other}/library`, 'GET')
  ).status
  report.cross_account_recommendations_status = (
    await status('/recommendations', 'POST', { steamid64: other })
  ).status
  report.logout_status = (await status('/auth/logout', 'POST')).status
  report.logged_out_sync_status = (
    await status(`/users/${id}/library/sync`, 'POST')
  ).status
  report.logged_out_preference_status = (
    await status(`/users/${id}/preferences/${game?.steam_app_id ?? 1}`, 'PUT', {
      preference: 'liked',
    })
  ).status
  report.logged_out_library_status = (await status(`/users/${id}/library`, 'GET')).status
  return report
}

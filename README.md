# GameRec

**Current access policy:** Library, preferences, sync and personalised recommendations
require a matching Steam session. Public discovery/search/explanations remain open.
Use **Sign in with Steam** in the app; Library and Recommendations automatically
use the verified account through either sidebar link. See [Phase 2C](#steam-login-and-account-experience--phase-2c).
Earlier phase results below are historical; ID-only live launchers are retired.

## Discovery frontend — Phase 1

A React + strict TypeScript discovery interface lives in `frontend/`. Vite and
Tailwind provide the development/build foundation, React Router keeps search
state shareable, TanStack Query manages API requests, and Lucide supplies the
small icon set. The explanation drawer uses Radix Dialog for keyboard focus,
Escape dismissal and focus restoration. No account or login is required for
this discovery slice.

With the existing backend `.env` configured, start the search dependencies and
the optional frontend service:

```bash
docker compose --profile embeddings --profile frontend up -d --build api embeddings qdrant redis frontend
```

Open **http://localhost:5173**. The frontend image installs locked dependencies
with `npm ci`; Node 24 runs inside Docker, so no host Node installation is needed.
Source changes reload automatically. The frontend port is bound to localhost.
For explanations, also start the existing Ollama profile/model using the local
LLM instructions below; search works independently of Ollama.

After changing frontend dependencies, refresh the isolated dependency volume:

```bash
docker compose --profile frontend build frontend
docker compose --profile frontend run --rm frontend npm ci
docker compose --profile frontend up -d frontend
```

### Interface and design

- A compact sidebar becomes a top navigation on mobile. Discover and
  Recommendations and Library are available.
- The starting screen offers three editable examples; neither selecting an
  example nor typing sends a request. Enter or Search submits the form.
- Query, filters and the 20/50/100 result limit are stored in the URL. Search
  preserves the server's ordering and displays returned counts, without
  suggesting that these are all catalogue matches or offering false pagination.
- Genre and category inputs accept comma-separated Steam labels. Matching is
  OR within each group and AND across groups and other constraints. Release
  bounds are inclusive. The existing 100-review eligibility floor still applies.
- Cards show real names and result positions. Steam header artwork is requested
  by app ID and has a fixed-size fallback on failure. The search API does not
  expose genres, review counts, dates or prices, so cards do not invent them.
- Opening artwork/title inspects a game without inference. **Why this game?**
  or **Explain this match** explicitly calls `POST /explanations/search` using
  the submitted query, even if the input has since been edited. Explanations
  show loading, error/retry and matching-feature states. No automatic inference
  or request retry runs in the background.

The visual system uses charcoal `#111214`, surfaces `#1B1D20`/`#24262A`, text
`#F2F2EF`/`#A6A8AD`, borders `#303238`, and locally bundled Inter Variable.
Tokens live in the stylesheet's Tailwind theme/CSS variables. The type scale is
12/14/16/20/28 px, with compact metadata styles; spacing follows
4/8/12/16/24/32/48 px and control/card radii are 4/6 px. Artwork supplies colour.
Keyboard focus, touch targets, missing artwork and reduced motion are supported.

```text
frontend/
  src/app/                  routing and application shell
  src/api/                  typed existing API contracts and fetch client
  src/components/           shared game cards, artwork, drawers and feedback states
  src/features/discovery/   search/filter form, URL state, explanation drawer
  src/features/recommendations/ page, development profile selector and query hook
  src/features/library/     stored library, playtime formatting and pagination
  src/styles/               design tokens and responsive styles
  tests/                    isolated browser checks and separate live verification
```

### API configuration

The browser uses `VITE_API_BASE_URL` (default `/api`). Vite strips `/api` and
proxies to `API_PROXY_TARGET` (default `http://api:8000`). Compose accepts both
as environment overrides; standalone Vite can use `frontend/.env` based on its
example. Only public frontend settings belong in `VITE_*` variables.

This integrates the unchanged `GET /search/games` and
`POST /explanations/search` contracts. No backend CORS change is needed with
the development proxy. A deployed static build needs an equivalent same-origin
API reverse proxy; Vite's development proxy is not deployment infrastructure.

### Frontend checks and browser verification

```bash
docker compose exec -T frontend npm run typecheck
docker compose exec -T frontend npm run lint
docker compose exec -T frontend npm run build
```

Run browser tests in a disposable container. Its browser image must match the
locked `@playwright/test` version (currently **1.65.0**). The following commands
use the default `gamerec_default` Compose network; replace it if you override
the Compose project name. Use your WSL UID/GID to keep artifacts user-owned.

```bash
docker run --rm --init --shm-size=1gb --user "$(id -u):$(id -g)" \
  --network gamerec_default -v "$PWD/frontend:/app" \
  -v gamerec_frontend_node_modules:/app/node_modules -w /app \
  mcr.microsoft.com/playwright:v1.65.0-noble npm test

# Separate live check: real search and two explicitly requested explanations.
docker run --rm --init --shm-size=1gb --user "$(id -u):$(id -g)" \
  --network gamerec_default -v "$PWD/frontend:/app" \
  -v gamerec_frontend_node_modules:/app/node_modules -w /app \
  mcr.microsoft.com/playwright:v1.65.0-noble npm run test:live
```

The isolated tests intercept external API/artwork calls. The live test does not
mock responses: it uses the actual Vite proxy, search services and explanation
endpoint. It saves desktop, tablet and mobile screenshots plus API evidence
under `frontend/live-results/`. Browser test output, builds and dependencies
are ignored by Git.

Local verification on 12 November 2026: 23 isolated browser tests and one live
integration check passed; the live query returned 20 games and both explicit
explanation requests returned HTTP 200. Desktop/tablet/mobile screenshots were
visually inspected. TypeScript, ESLint, production build, repository Ruff and
93 existing focused search/explanation/cache tests passed. These are local
checks, with no hosted CI claim.

Known limits: search covers only the indexed catalogue and its existing
candidate pool; response metadata is deliberately sparse; artwork URLs are
best effort; AI explanations still require human quality evaluation. This
search slice does not provide authenticated libraries. The Phase 2 adapter and
Phase 3 page below expose stored-profile recommendations. Production hosting
and authentication remain deferred.

The initial dependency audit reports a moderate `minimatch` advisory affecting
the ESLint/typescript-eslint development dependency chain (10 reported package
entries). The affected glob matcher is not part of the browser bundle. Avoid
forcing a major-version transitive override; update when the lint tools adopt
the patched dependency. See [the upstream advisory](https://github.com/advisories/GHSA-g3hv-75wg-m249).

## Recommendation API integration — Frontend Phase 2

Repository inspection found recommendation services and scripts but no HTTP
endpoint. A small read-only adapter now exposes **`POST /recommendations`**.
The Steam ID goes in JSON, keeping it out of browser URLs and ordinary access
logs:

```json
{"steamid64": "<locally configured decimal Steam ID>", "limit": 20}
```

The identifier must be a canonical positive unsigned 64-bit integer **string**,
without leading zeros. Numeric JSON identifiers are rejected to avoid precision
loss. `limit` is an integer from 1 to 100, default 20. The response contains
`total`, `limit` and `games`; `total` counts returned recommendations, with no
pagination or catalogue-wide count. Each game has:

- `steam_app_id` and nullable `name`.
- `similarity_score`, `popularity_score`, `review_quality`, `hybrid_score`,
  `affinity_score` and `recency_score`, from the existing `RankedCandidate`.

The adapter reuses profile construction, multi-source retrieval, owned-game
exclusion, batched metadata loading, review eligibility and hybrid ranking.
It matches the current manual script's 5,000 profile candidates, 10 seeds,
100 candidates per seed and 500-review minimum. Existing ranking weights remain
unchanged; MMR is not enabled or exposed. Results retain server ranking order.
The endpoint uses PostgreSQL and Qdrant with already stored library/preferences
and current embeddings. It does not fetch Steam data, regenerate embeddings,
call the query-embedding service or invoke Ollama.

Invalid inputs return 422, a missing own stored user returns 404, and unavailable
PostgreSQL/Qdrant dependencies return a safe 503. Existing users with no usable
profile or eligible candidates receive 200 with an empty `games` list. This
does **not** prove that their Steam library is empty. Phase 2B now requires a
matching authenticated session; supplying an ID alone grants no access.

### Typed frontend seam

`src/api/types.ts` defines `RecommendationOptions`, `RecommendationRequest`,
`RecommendedGame` and `RecommendationResponse`. The existing client exposes:

```typescript
getRecommendations(steamId: string, options?: RecommendationOptions, signal?: AbortSignal)
```

The reusable `useRecommendations(steamId, options)` hook accepts an optional
string identity and `{ limit?: number }`. Missing/blank IDs disable requests.
Its key includes the trimmed string ID and effective limit. It forwards abort
signals and uses the same QueryClient defaults as discovery (no automatic
retries/focus/reconnect refetching, 30-second stale time). No identifier is
converted to a JavaScript number or persisted by the application.

Phase 2 added the integration seam; Phase 3 below adds its page and navigation.
There is no library UI, debug application route, automatic recommendation
explanation generation or frontend scoring. A test-only HTML/React harness under
`frontend/tests/` exercises the hook through Vite and is absent from the
production build. Missing names remain nullable; genres, review counts, release
dates and artwork metadata are not supplied by this response.

### Recommendation verification

Use the regular frontend checks above; `npm test` includes the focused mocked
recommendation client/hook tests. The former ID-only launcher is retired because
it cannot authenticate account ownership. All its modes exit before reading an ID,
starting a browser or creating recovery data. Use the signed-in browser procedure
under Phase 2B. No cookie export, seeded development session or auth bypass is used.
Historical Phase 2 live results below predate this access-policy change.

Local Phase 2 verification on 12 November 2026: the real proxy/client/hook request
returned HTTP 200 with 20 recommendations in descending hybrid-score order.
All 35 mocked frontend browser tests passed (23 existing discovery/explanation
tests plus 12 recommendation tests). All 192 selected backend tests passed,
including 47 new endpoint/orchestration cases, two launcher privacy checks and
existing recommendation, search and explanation regressions. TypeScript,
ESLint, production build, Ruff lint and affected-file formatting checks passed.
The Recommendations page below now uses this verified contract; library UI
and authentication remain separate work.

## Personalised recommendations page — Frontend Phase 3

Open **http://localhost:5173/recommendations** or choose **Recommendations** in
navigation. The page uses the existing `useRecommendations` hook and
`POST /recommendations` with its default limit of 20. The backend order is kept;
no browser scoring, new endpoint, library refresh or Ollama call is involved.

Phase 2C replaces the former development profile selector with the verified
`/auth/me` identity in both development and production. Signed-out visitors see a
sign-in requirement; signed-in users load their own recommendations automatically.
No account ID is entered, stored in browser storage or passed between pages.
Both the sidebar and Library's **View recommendations** link work directly.

The page shares discovery's landscape cards, artwork fallbacks and feedback
components. Missing/blank names display `Steam app <ID>`. Cards omit unavailable
genres, reviews, descriptions, dates and prices, and keep ranking internals out
of the interface. **View details**, artwork and titles open a keyboard-accessible
Radix drawer with a Steam link. Recommendation-specific explanations are deferred:
the existing explanation route needs a natural-language search query.

Loading uses skeletons and disables Refresh. An empty result explains that usable
profile data/candidates may be insufficient, without claiming the library is
empty. Unknown-user, invalid-input, dependency and network failures show the
existing recommendation messages with explicit retry. Shared query caching and
cancellation remain in effect, with no automatic retries. Refresh explicitly
requests fresh results. Library navigation is activated in Phase 4 below.

Run the frontend checks and mocked browser command above. For a separate real
page check using the configured local `STEAMID64_TEST` privately:

```bash
# Retired in Phase 2B; use its signed-in browser verification instead:
# python3 frontend/scripts/verify_recommendations.py --page
```

The launcher reuses the Phase 2 configuration capture and redaction. Live traces,
automatic screenshots and video remain off; this page check explicitly captures
ID-free desktop, tablet, mobile and details-drawer screenshots after the input
has cleared. Images and redacted status/count evidence are saved under ignored
`frontend/live-results/recommendations-page/`. The regular launcher without
`--page` retains the original client/hook check. Do not put a personal ID in
command examples, committed fixtures or frontend configuration.

Local verification on 12 November 2026:

- **48 mocked browser tests passed**: 23 discovery/explanation, 12 client/hook and
  13 page cases (navigation, validation, states, sparse metadata, ordering,
  caching, privacy, drawer focus/Escape, reduced motion and 320–1440 px layouts).
- A separate real page check returned **HTTP 200 and 20 recommendations**, kept
  response order, and made **zero explanation requests**, including inspection.
  Desktop/tablet/mobile screenshots were visually reviewed.
- TypeScript, ESLint and production build passed. The selector and browser test
  harness are absent from production JavaScript. Three Python launcher privacy
  tests, Ruff and launcher/test formatting checks passed.
- Backend code did not change in this phase; its previously recorded Phase 2
  tests were not rerun. CI remains intentionally removed; verification is local.

Limitations: development identity selection only; account ownership/access
control and personalised explanation evidence remain deferred; Phase 4 below
adds read-only library browsing.
Recommendations depend on stored data and current indexed embeddings. Artwork is
best effort (one real result used the unavailable-artwork fallback). No manually
labelled relevance improvement is claimed. Recommended next phase: design real
account identity and access control before exposing stored libraries to users.

## Stored Steam library — Frontend Phase 4

Open **http://localhost:5173/library** or choose **Library** in navigation.
Sign in with Steam to load your own stored games automatically. The page reads
PostgreSQL and does not automatically import or synchronise Steam data. Sync is
an explicit action available to an authenticated owner in all builds.

```bash
docker compose --profile frontend up -d --build api frontend
```

The normal route is `/library`, with optional `?page=2`. Reload and Back/Forward
preserve pagination without putting an account ID in the page URL. Legacy
`/library/:steamid64` links only redirect when they match the verified owner;
other/invalid IDs show Library unavailable and do not request that account's data.

### Data and presentation

`getSteamLibrary(steamId, options?, signal?)` uses the existing
`GET /users/{steamid64}/library?limit=50&offset=0` endpoint through the central
client. Options contain optional `limit` and `offset`. `useSteamLibrary` keys
include the trimmed ID, limit and offset, forward cancellation and inherit
existing retry/refetch defaults. Data from another identity or page is not used
as placeholder content.

The wire types preserve `steamid64`, `total_games`, `limit`, `offset`, nullable
`library_last_synced_at`, and each game's ID, name and two nullable playtime
fields. Compact artwork rows preserve server ordering (currently app ID), with
missing-art/name fallbacks and the shared accessible details drawer/Steam link.
No genres, reviews, dates, scores or library-wide playtime total are invented.

- Lifetime zero reads **Not played**; 45 minutes reads **45 min**, 90 reads
  **1.5 hrs**, and 1,200 reads **20 hrs**. Hours round to one decimal.
- Unknown lifetime playtime reads **Playtime unavailable**. Unknown recent
  playtime is omitted; recorded recent values are labelled **Past 2 weeks**.
  Playtime reflects the last stored sync, not a live Steam update.
- The compact overview shows the actual stored-game count and last sync in UTC.
  A null timestamp reads **Sync time unavailable**.
- Pages contain 50 games. Previous/Next and ranges use response pagination and
  the actual returned count. Malformed/unsafe page parameters resolve to page 1;
  an out-of-range page offers a return to the first page. No whole-library fetch,
  client-side sorting or filtering is added.
- No-ID, loading, unknown stored user (404), empty storage, network/server failure
  and explicit retry states are distinct. Empty storage does not establish that
  the Steam account has no games.

After a successful library response, **View recommendations** explicitly carries
the string ID to `/recommendations` through router state. That state is validated
and cleared once consumed, keeping recommendation URLs unchanged. The existing
recommendation hook then requests suggestions. Viewing Library alone never
requests recommendations, preferences or explanations. No ranking code changes.

### Local verification

Run the regular frontend TypeScript, ESLint, build and browser commands above.
The full mocked suite includes 22 new Library cases. For a separate live check
using the existing locally configured `STEAMID64_TEST`:

```bash
# Retired in Phase 2B; use its signed-in browser verification instead:
# python3 frontend/scripts/verify_recommendations.py --library
```

The launcher uses the existing configuration capture and private test environment.
Traces, video and automatic screenshots are off; explicit screenshots mask the
ID input and selected-ID text. Text failure artifacts and console diagnostics are
redacted before retaining them. Reports contain counts/status only, under ignored
`frontend/live-results/library/`. Synthetic loading/empty screenshots are under
ignored `frontend/test-results/`. Never commit a personal ID or raw library URL
in fixtures, reports or screenshots.

Verification on 12 November 2026: all **70 mocked browser tests passed**, including
22 new Library cases; the Library selection was rerun after the mobile refinement.
TypeScript, ESLint, production build, six launcher privacy tests and focused
Ruff/formatting checks passed. The live endpoint returned **HTTP 200**, with
**65 stored games: 50 on page one and 15 on page two**. Back navigation reused
cached results, and an explicit recommendation handoff returned HTTP 200 with
20 suggestions. Populated,
loading, empty and pagination layouts were reviewed on desktop/mobile; tablet and
320–1440 px overflow, keyboard focus and reduced motion were checked. Backend
code is unchanged and its tests were not rerun. CI remains intentionally removed.

Limitations: existing stored data only; authentication/access control, Steam sync,
preference editing, server-side sorting/filtering and library-wide playtime totals
remain outside this phase. Artwork is best effort. No relevance-quality claim is
made. Phase 2B now restricts the full response to the signed-in owner. Public
SteamID-based browsing is no longer supported by this response contract.

## Manual Steam library synchronisation — Frontend Phase 5

**Sync Library** explicitly refreshes the signed-in owner's stored library.
The backend requires a valid Steam session, matching path ID and trusted request
origin. `MANUAL_LIBRARY_SYNC_ENABLED` and the `STEAMID64_TEST` allowlist no longer
control access. `MANUAL_LIBRARY_SYNC_COOLDOWN_SECONDS=60` still controls pacing.
`sync_allowed` is true in an authorized owner Library response. The current UI
shows the control for the authenticated owner in development and production,
using the existing backend capability flag.

Steam must make the account's game details visible to the API; unavailable data
is not an empty library. See the [official GetOwnedGames documentation](https://partner.steamgames.com/doc/webapi/IPlayerService#GetOwnedGames).

### Sync contract and behaviour

`POST /users/{steamid64}/library/sync` accepts an exact 17-ASCII-digit string path
identifier and no request body. Steam sign-in creates/reuses the User first; an
authenticated account without stored games can perform its first sync.
The page exposes the action after an existing library has loaded. A completed
response contains:

```json
{
  "steamid64": "<configured account ID>",
  "status": "completed",
  "processed_games": 65,
  "total_games": 65,
  "library_last_synced_at": "2026-11-13T00:00:00Z",
  "metadata_enrichment": "partial"
}
```

The numbers/time above illustrate the schema. `processed_games` counts returned
ownership entries; `total_games` is the actual stored count. Imports atomically
upsert ownership/playtime, create needed catalogue stubs and update the successful
sync timestamp. They retain existing rows absent from a later response, including
valid empty responses. Fetch/validation failures do not change ownership or the
successful timestamp. CLI import behaviour is unchanged.

Core import has a 120-second budget. After its commit, metadata enrichment gets
30 seconds, at most 10 returned library games, batch size 10 and HTTP concurrency
2, with existing pacing/retries. `partial` means work remains or was interrupted;
it never undoes a completed ownership import. `complete` means no returned game
remains awaiting a metadata sync, including terminal unavailable Store responses;
it does not guarantee every descriptive field is available. No catalogue-wide
work or embedding regeneration runs. New games can remain unindexed until the
existing embedding workflow is run separately.

Errors use `detail: {"code": "...", "message": "..."}`:

| HTTP | Code | Meaning |
|---|---|---|
| 422 | FastAPI validation detail | Invalid path ID |
| 403 | `sync_forbidden` | Disabled, wrong environment or disallowed account |
| 409 | `sync_in_progress` | Same-account import is running |
| 429 | `sync_cooldown` | Wait for `Retry-After` seconds |
| 503 | `steam_unavailable` | Owned-game data unavailable/private |
| 502 | `steam_upstream_error` | Failed/unusable Steam response |
| 504 | `sync_timeout` | Timeout; refresh stored data before retrying |
| 503 | `database_unavailable` | Storage unavailable; refresh before retrying |

Accepted attempts start a configurable 60-second cooldown before contacting
Steam, including failures. Rejections do not extend it. A recent persisted
successful-sync timestamp also protects against immediate repeat imports; longer
Steam `Retry-After` delays extend protection. Concurrent requests are rejected
immediately. Coordination and failed-attempt cooldowns are in process: they do
not survive API restarts or coordinate workers. **Use one local API worker.**

The typed `syncSteamLibrary` / `useSyncSteamLibrary` mutation has no automatic
retries or optimistic ownership updates. Its client timeout is 180 seconds; the
Vite proxy remains 200 seconds. Pending state survives navigating away/back.
Confirmed completion invalidates all library pages and recommendation queries
for the original account only; active data refetches and inactive data becomes
stale. Stored rows remain visible during sync/background refresh, and a failed
refresh offers a read-only retry. Timeout/lost-response messages advise reading
stored data first because the import might already have committed.

### Local verification

The ordinary `--library` verification remains read-only. This separate mode
**performs one real import** of the configured account and checks immediate
cooldown, persisted count/playtime/timestamp, and masked desktop/mobile layouts:

```bash
# Retired in Phase 2B; use its signed-in browser verification instead:
# python3 frontend/scripts/verify_recommendations.py --library-sync
```

Live traces/video/automatic screenshots are off. Explicit screenshots mask
identity fields; diagnostics are redacted. Only status/count evidence is saved
under ignored `frontend/live-results/library-sync/`. Do not publish personal IDs
or raw library URLs. Mocked tests and isolated PostgreSQL tests are separate from
this live evidence. CI remains intentionally removed; verification is local.

Verification on **13 November 2026**:

- **369 focused Python tests passed**, including 35 new manual-sync tests and
  nine launcher privacy tests. **24 isolated PostgreSQL tests passed**, including
  eight new orchestration cases. No automated persistence test used development
  data. The disposable PostgreSQL test server was removed after verification.
- **84 mocked browser tests passed** (14 new sync checks); the 14 sync cases were
  rerun after fixture cleanup. A separate built production-preview check passed.
  TypeScript, ESLint, production build, repository Ruff and affected-file
  formatting checks passed.
- One real sync returned **200**, processed/stored **68 games** (previous stored
  count: 65), and reported metadata enrichment **complete**. The persisted sync
  timestamp matched the response and advanced; all returned playtime values were
  checked. An immediate second POST returned **429 / Retry-After: 60** before
  another Steam fetch.
- After cooldown, a temporary closed outbound HTTP proxy produced **502 /
  steam_upstream_error**. The complete ownership/playtime snapshot and successful
  timestamp were unchanged. A further attempt returned **429**, proving failed
  attempts retain cooldown. Normal API configuration was restored; the temporary
  sync flag is disabled. No second successful live import was needed.
- Masked desktop/mobile success and mocked partial-enrichment screenshots were
  visually reviewed. Redacted live evidence is under
  `frontend/live-results/library-sync/`; mocked evidence is under
  `frontend/test-results/`. No quality/relevance ratings were fabricated.

Focused backend commands (run from the repository root):

```bash
uv run pytest -q tests/test_manual_library_sync.py tests/test_user_library_api.py tests/test_verify_recommendations_frontend.py
# Requires TEST_POSTGRES_ADMIN_URL pointing to a dedicated /gamerec_test_admin DB.
uv run pytest -q --run-integration tests/integration/test_manual_library_sync.py tests/integration/test_library_sync.py tests/integration/test_library_api.py
```

The existing browser command runs all mocked regressions. To repeat the production
visibility guard, start `npm run preview -- --port 5174` in the frontend container
(after building), then use the same matched Playwright container command above
with `npm run test:production` in place of `npm test`. Stop preview afterwards.

## User preferences and recommendation feedback

### Pipeline inspection before this change

The existing `user_game_preferences` table stores one row per account/game,
including `liked`, `disliked` and legacy `neutral`. Clearing a row restores
unrated behaviour. Existing PUT/DELETE endpoints persist these values, but
previously lacked a write allowlist and allowed ratings for unowned catalogue
games. Library responses did not expose preferences and the UI had no controls.

The current personalised pipeline already reads owned-game preferences when
constructing the profile, selecting retrieval seeds and calculating genre/category
affinity. It uses liked **1.0**, disliked **−0.6**, neutral **0.4**, and unrated
**0.5**. With at least three valid embedded games played for 300 minutes each,
profile coefficients multiply these values by logarithmic playtime; otherwise
existing preference-only/unweighted fallbacks apply. The summed vector is L2
normalised. Disliked games are excluded from positive seed selection.

Affinity uses signed preference weights and capped logarithmic playtime, with
signed genre TF–IDF and category frequencies. Final weights are **0.30 similarity,
0.30 popularity, 0.10 review quality, 0.25 affinity and 0.05 recency**. Optional
MMR is disabled by the HTTP orchestration. Recommendations have no backend result
cache; the frontend uses TanStack Query. Redis caches search explanations only.

The specific defect found was that sufficient-history profile weighting and
playtime affinity discarded explicit feedback at zero playtime. This phase fixes
that boundary while preserving the existing preference and hybrid multipliers.

### Enable preference editing

Sign in through `/api/auth/steam/login`, then load your own Library. The server
requires a matching session and trusted origin for both PUT and DELETE. The
former `USER_PREFERENCE_WRITES_ENABLED` flag and test-account allowlist are retired.
Controls require the verified owner session and `preference_writes_allowed` in
both development and production builds.

### Saved preference contract and UI

- `PUT /users/{steamid64}/preferences/{steam_app_id}` accepts
  `{"preference":"liked"}` or `"disliked"`; legacy `"neutral"` remains accepted.
  The existing response contains identity, preference and update timestamp.
- `DELETE` at the same URL returns **204** and removes the row, restoring unrated
  behaviour. It is idempotent. `null` is a Library response state, not a PUT value.
- New/updated ratings require stored ownership. Existing unowned legacy records
  remain readable through GET preferences and clearable through DELETE.
- Write IDs must be exactly 17 ASCII digits. Disallowed writes return **403**
  (`account_forbidden` or `csrf_failed`); missing/invalid sessions return **401**.
  Unknown game or unowned PUT target returns **404**
  (`user_not_found`, `game_not_found`, `game_not_owned`); invalid inputs **422**;
  unavailable storage **503** (`database_unavailable`). Errors do not expose SQL
  parameters or raw account IDs.
- Library games now contain `preference: "liked" | "disliked" | "neutral" | null`.
  A user-scoped outer join loads this with the existing paginated query, without
  per-game requests. Stored preferences survive syncs; writes do not alter
  ownership, playtime, sync timestamps, game metadata or embeddings.

Clicking a selected Like/Dislike clears it; the opposite action switches it.
Buttons expose `aria-pressed`, keyboard focus and text states. Legacy neutral
ratings retain their weight and show **Neutral (legacy)** with an explicit Clear
control. No new Neutral button is introduced.

The typed `updateGamePreference` client and `useUpdateGamePreference` hook use
per-account/game mutation keys. Pending writes survive navigation and reject
conflicting clicks. The previous confirmed state stays visible while saving;
failed/uncertain writes offer read-only reload and explicit retry, with no
optimistic changes or automatic retries. Success cancels obsolete account-scoped
reads, patches only confirmed cached rows, and invalidates that account's Library
pages and recommendation limits. Active queries refetch; inactive ones become
stale. This does not automatically generate recommendations from Library or call
Steam, Ollama or Qdrant writes. The existing View recommendations action retrieves
fresh results through the backend and preserves their ordering.

### Actual preference mathematics

Let `t_i = max(0, lifetime minutes)` and let `b_i` be the existing preference
multiplier: liked 1.0, disliked -0.6, neutral 0.4, unrated 0.5. The shared
`apply_explicit_playtime_floor` applies:

```text
a_i = max(1, ln(1 + t_i))   for liked/disliked
      ln(1 + t_i)           otherwise
```

`construct_profile_vector` first discards invalid/non-finite/zero-norm vectors.
With sufficient play history, it computes `w_i = b_i * a_i / sum(a)` and returns
`L2_normalise(sum(w_i * game_vector_i))`. The common denominator cancels under
final normalisation. Thus liking an otherwise equal game doubles its coefficient
relative to unrated; disliking reverses the sign, from +0.5 to -0.6. A 200-hour
dislike has coefficient proportional to `-0.6 * ln(12001)`, not a large positive
playtime signal. A five-hour like has positive strength `ln(301)`. Zero/unknown
playtime explicit feedback now has base strength +1.0 or -0.6; the floor also
covers very small playtimes below that logarithmic strength.

With insufficient play history, existing fallbacks remain: if any valid explicit
preference exists, use preference multipliers alone; otherwise use uniform
library weights. Only-dislike/non-positive or numerically cancelled profiles
return no usable profile, producing the existing successful empty recommendation
response. This does not establish an empty library.

`select_representative_seed_games` excludes dislikes, orders eligible games by
signed preference strength times the floored logarithmic playtime, then preference
strength and app ID. Liked unplayed games remain eligible. Qdrant retrieves profile
and seed candidates; owned games are removed and current PostgreSQL vectors
recompute each candidate's cosine similarity to the preference-adjusted profile.

`build_user_affinity_profile` caps positive logarithmic playtimes at their existing
90th percentile, then applies the same explicit-feedback floor before multiplying
by `b_i`. Signed contributions accumulate by gameplay genre/category. Genre TF is
`sign(f) * ln(1 + abs(f))`, with smoothed IDF
`ln((catalogue_size + 1)/(genre_count + 1)) + 1`; generic Free To Play/Early Access
labels are excluded. Genre and category maps are separately normalised by maximum
absolute score. Candidate affinity is **0.7 × mean genre affinity + 0.3 × mean
category affinity**. Likes/dislikes can reinforce or cancel shared features;
legacy neutral and unrated zero-playtime behaviour remains unchanged.

The existing `rank_candidates` formula remains:

```text
hybrid = 0.30 * similarity + 0.30 * popularity + 0.10 * Wilson review quality
       + 0.25 * affinity   + 0.05 * release recency
```

Preferences affect profile/seed retrieval, recomputed semantic similarity and
signed affinity. Popularity (log review count), Wilson quality and release recency
remain independent. No additional dislike penalty is added; semantic and affinity
feedback retain their existing roles. Optional MMR remains disabled. Preferences
cannot guarantee a particular top-20 move: candidate coverage, shared metadata,
500-review eligibility, popularity and the other components still matter. Missing
current embeddings prevent vector feedback; genre/category feedback can still
matter if other owned games provide a usable profile and metadata is available.

### Preference verification

The explicit live mode temporarily rates two originally unrated, embedded owned
games, compares actual HTTP recommendations and profile/affinity signals, and
clears both test preferences in browser-independent host cleanup:

```bash
# Retired in Phase 2B; use its signed-in browser verification instead:
# python3 frontend/scripts/verify_recommendations.py --preferences
```

Run it only after enabling development preference writes. It does not sync Steam,
update metadata or regenerate embeddings. Traces, video and automatic screenshots
are disabled; explicit screenshots mask identity fields. Redacted comparisons,
masked images and a protected restoration journal are saved under ignored
`frontend/live-results/preferences/`. An unresolved recovery journal blocks another
run. If restoration fails, use its listed public app IDs to clear only the test
records for the same configured account, then verify their absence. Do not delete
an unresolved recovery journal or publish personal IDs/raw library URLs.

Automated persistence tests use UUID-isolated PostgreSQL databases. Unit tests mock
external dependencies; the persisted recommendation test uses the actual profile,
affinity and ranking functions with mocked Qdrant retrieval. Existing search,
explanations, Library sync and recommendation behaviour remain regression gates.
CI remains intentionally removed; verification is local. No new tables, migrations,
models, ranking sliders, infrastructure or authentication are introduced.

Local verification on **13 November 2026**:

- **454 focused Python tests passed**, including 51 new preference API/math
  cases and 22 launcher/tooling checks. **47 isolated PostgreSQL tests passed**,
  covering persistence, Library sync and the actual recommendation pipeline with
  mocked Qdrant. The disposable test server was removed afterwards.
- **98 mocked browser tests passed**, including 14 preference checks, plus one
  separate production-preview guard. TypeScript, ESLint, production build,
  repository Ruff and affected-file formatting checks passed. Production hides
  write controls even when a mocked API advertises both capabilities.
- Four real browser stages (baseline, like, like + dislike, restored) each
  returned **HTTP 200 / 20 recommendations** through the Vite proxy and existing
  typed client/hook. The catalogue guards and successful-sync timestamp stayed
  unchanged. No Steam import, metadata update or model inference was requested.
- Liking **Monster Hunter: World** changed its signed pre-normalisation profile
  coefficient from **0.013429054 to 0.026858107**. Disliking **Terraria**, despite
  **42,081 recorded minutes**, changed its coefficient from **+0.014176065 to
  −0.017011278**. Both originally absent test records were deleted afterwards.

One observed returned recommendation:

| MONSTER HUNTER RISE | Rank | Similarity | Affinity | Hybrid score |
|---|---:|---:|---:|---:|
| Baseline | 10 | 0.644165 | 0.836353 | 0.744478 |
| After like | 10 | 0.657939 | 0.840326 | 0.749603 |
| After like + dislike | 8 | 0.663065 | 0.855017 | 0.754814 |
| Restored | 10 | 0.644165 | 0.836353 | 0.744478 |

The like alone changed scores without moving this game's rank. The two signals
changed the final ordering; this does **not** establish improved relevance.
Terraria's cosine to the whole profile remained positive (0.717995 → 0.662927)
because other library vectors share characteristics; its own coefficient was
negative. Popularity, Wilson quality and recency were unchanged for common
returned games. Clearing both test ratings restored the profile and affinity
fingerprints and **all original top-20 IDs, positions and score components exactly**.

The complete four-stage top-20 exports, signed signals, configuration and
comparison are in the local ignored `frontend/live-results/preferences/comparison.json`
and `report.md`; `recovery.json` confirms restoration. Masked real liked/disliked
and mocked all-state desktop/mobile screenshots were visually reviewed.
That historical run restored its original configuration. Phase 2B now authorizes
preference writes through Steam sessions; the old development flags are retired.

Focused commands, alongside the existing frontend checks/browser command:

```bash
uv run pytest -q tests/test_preferences_api.py tests/test_preference_feedback.py tests/test_verify_recommendations_frontend.py
# Only with TEST_POSTGRES_ADMIN_URL set to a dedicated /gamerec_test_admin DB:
uv run pytest -q --run-integration tests/integration/test_preferences.py tests/integration/test_preference_recommendations.py tests/integration/test_library_api.py
```

Historical Phase 1 limitations included one development account and public reads;
Phase 2B replaces that access policy. Remaining limitations include limited indexed catalogue/metadata and no promise that one rating changes a specific
rank. Only-dislike histories still return no usable positive recommendation profile.
No manual relevance or explanation-quality labels were added. The shared floor is
proved with deterministic zero-playtime fixtures; the live chosen games had nonzero
playtime. Existing best-effort artwork limitations remain.

## Steam authentication — Phase 2A

The backend supports Steam OpenID 2.0 and opaque PostgreSQL sessions. Phase 2B
connects sessions to owner-only API access; Phase 2C adds frontend sign-in/out and
automatic account selection.

### Configuration and local startup

Authentication is disabled by default. For the agreed local Vite proxy setup,
configure the private backend `.env` (these values contain no Steam credentials):

```dotenv
AUTH_ENABLED=true
AUTH_PUBLIC_BASE_URL=http://localhost:5173/api
AUTH_FRONTEND_ORIGIN=http://localhost:5173
AUTH_SUCCESS_PATH=/discover
AUTH_SESSION_TTL_SECONDS=604800
AUTH_LOGIN_TTL_SECONDS=600
AUTH_COOKIE_SECURE=false
AUTH_SESSION_COOKIE_NAME=gamerec_session
AUTH_LOGIN_COOKIE_NAME=gamerec_login
```

`AUTH_COOKIE_SECURE=false` is an explicit override restricted to HTTP loopback
URLs in development. The code defaults to Secure=true; non-development/HTTPS
configuration requires HTTPS public URLs and secure cookies. Cookie base names
must be distinct simple identifiers. Unsafe origins, credentials/query/fragment
in URLs and nonlocal success paths are rejected when auth is enabled. Disabled
auth needs no origin configuration and returns safe 503s on its endpoints.

This phase uses the existing services and a reviewed, handwritten additive
migration, **7c91f6a2d804** after **2f86da100328**:

```bash
docker compose --profile frontend up -d api db frontend
# Inspect the migration before applying it on another checkout/environment.
docker compose exec -T api uv run --no-sync alembic upgrade 7c91f6a2d804
docker compose up -d --no-deps api
```

The current local workspace has already received this migration and local config.
Use **localhost:5173** consistently; switching between localhost and 127.0.0.1
changes cookie hosts. Production needs an equivalent same-origin HTTPS proxy.
SameSite=Lax does not support an unrelated cross-site frontend through ordinary
credentialed fetches; that deployment topology is not provided here.

### Endpoint contracts and flow

Public proxy paths have `/api` before the following backend routes:

| Endpoint | Behaviour |
|---|---|
| `GET /auth/steam/login` | Set a temporary protected cookie and redirect to Steam with 303 |
| `GET /auth/steam/callback` | Verify the browser-bound assertion, set session cookie, clear temporary cookie and redirect to configured `/discover` |
| `GET /auth/me` | Return only `{"steamid64":"<verified account>"}`, or 401 |
| `POST /auth/logout` | Require trusted request origin, delete the current session and clear cookies; idempotent 204 |

A login creates independent 32-byte random public and private values. Only the
private cookie's SHA-256 hash is stored. The public `login_id` is included inside
`openid.return_to` and is not a bearer session token. Callback processing requires
the matching private HttpOnly cookie and an unexpired, unused transaction. The
transaction is atomically consumed before provider verification; failures require
a fresh sign-in and no assertion is automatically retried. Malformed/unbound
callbacks do not clear an unrelated pending browser transaction.

The Steam-specific async verifier uses the fixed HTTPS Steam endpoint, validates
namespace/provider/identity/return URL and required signed fields, rejects duplicate
or conflicting parameters, and forwards assertion values unchanged with
`openid.mode=check_authentication`. Additional valid signed fields, including the
signed-field list itself, remain intact. A positive Steam verification response
and matching bounded Steam HTTPS XRDS discovery are required before using the
SteamID64. HTTP identity URL labels documented by Steam are accepted as identifiers;
all network traffic still uses HTTPS with certificate verification enabled.
See [Steam authentication](https://partner.steamgames.com/doc/features/auth) and
[OpenID verification](https://openid.net/specs/openid-authentication-2_0.html#verification).

Nonces must use the expected UTC format, be at most 10 minutes old and no more
than two minutes in the future. PostgreSQL enforces unique accepted nonce hashes.
Remote verification/discovery has a 20-second overall budget, five-second connect
limit, bounded response bodies, no redirects and no automatic retries. Clients
and response streams close on failure/cancellation.

On success, a single transaction records the verified nonce, inserts or reuses the
existing `User.steamid64` key, revokes the presented previous session and stores
only a SHA-256 hash of a new 32-byte opaque token. The raw token exists only in
memory and the browser cookie. Login does not import Steam data, create ownership,
reset preferences or call Qdrant/Ollama. The session has fixed seven-day expiry;
activity does not extend it. Logout affects the current session, not other browsers.

### Storage, cookies, CSRF and privacy

The migration adds only `auth_sessions` and `steam_login_transactions`, their
primary/unique/foreign-key constraints and expiry indexes. Consumed transactions
retain nonce hashes until their acceptance window closes, including after logout.
Login traffic removes at most 100 expired rows per auth table per request; nonce
retention is respected. Expiry checks work even when cleanup has not run. Downgrade
removes only these auth tables and therefore invalidates authentication state.

Both cookies are HttpOnly, host-only and explicitly SameSite=Lax. HTTPS uses
`__Host-` prefixes, no Domain attribute and explicit Path=/, required for browser
host binding; local HTTP uses unprefixed names. No token is put in JSON or browser
storage. `get_current_user` in `gamerec.api.auth` resolves only an existing,
unexpired session and User. Duplicate/malformed cookies are rejected.

Logout's reusable trusted-origin guard checks the exact configured Origin. Only
when Origin is absent may Referer establish a trusted origin; missing/null or
untrusted sources receive 403. It never derives trust from Host/forwarded headers.
The cross-site Steam callback uses the independent login-transaction protection
rather than the logout Origin rule. CORS permits credentials only for the explicit
frontend origin, with explicit methods/headers; it is not the CSRF defense.
Vite's own CORS layer is disabled so it cannot overlay broader headers or intercept
API preflight decisions. Same-origin frontend operations need no Vite CORS layer.

Auth responses carry `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.
Application/Uvicorn callback logs redact query strings; HTTPX/HTTPcore redact
identity URLs, and Vite sanitises callback proxy errors before its default logger.
Expected failures log only safe categories. Deployment must separately redact
callback queries at ingress/CDN/proxy logs and avoid credential/wire logging.
TLS termination, trusted origins and proxy configuration remain deployment duties;
local HTTP verification does not establish production-ready security.

Invalid/replayed callbacks use safe 400 errors; malformed provider responses 502,
provider/database unavailability 503 and timeouts 504. No error echoes assertions,
cookies, SQL parameters or account identities. Phase 2B below replaces the former development write allowlists with session
ownership checks on user-specific endpoints.

### Automated and manual verification

Focused backend checks:

```bash
uv run pytest -q tests/test_steam_openid.py tests/test_auth_config.py tests/test_auth_api.py tests/test_auth_privacy.py
# Only with TEST_POSTGRES_ADMIN_URL targeting a dedicated /gamerec_test_admin DB:
uv run pytest -q --run-integration tests/integration/test_auth_sessions.py tests/integration/test_auth_migration.py
```

Existing frontend typecheck/lint/build and mocked-browser commands still apply.
A separate non-login HTTP check against the running proxy is available using the
same matched browser-container command above, replacing `npm test` with:

```bash
npm run test:auth:live
```

It checks redirect/cookie attributes, 401/403/204 behaviour and trusted/untrusted
CORS without following Steam or entering credentials. It creates only short-lived
login transactions. Traces, video and screenshots are disabled; retained evidence
contains status/boolean values, never cookies or assertions.

**Local status — 13 November 2026:** migration SQL reviewed, applied from the
verified previous head, existing data fingerprints unchanged, and `alembic check`
reports no new operations. **610 focused Python tests**, **62 isolated PostgreSQL
tests**, and **100 mocked browser checks** pass. TypeScript, ESLint, production
build, repository Ruff and affected Python formatting checks pass. One separate
live HTTP proxy check passes without Steam credential entry. Actual proxy checks
confirm trusted preflight 200, untrusted
preflight 400, no credentialed read permission for another localhost origin,
protected temporary cookies, private response headers and actual Uvicorn callback
query redaction. Evidence is under ignored `frontend/live-results/auth/`.
**Real Steam sign-in, authenticated `/auth/me`, real logout and second-login data
preservation remain pending the assisted browser check.** No live login is claimed.

Manual checklist (use your browser, not test automation):

1. Open **http://localhost:5173/api/auth/steam/login** and authenticate only on
   Steam's HTTPS site, using the account whose library is already stored.
2. After returning to Discover, privately inspect `/api/auth/me`; confirm the
   returned identity matches your account without sharing it.
3. In browser DevTools → Application → Cookies, confirm `gamerec_session` is
   HttpOnly, SameSite=Lax and host-only. The temporary login cookie is cleared.
4. From the Discover tab's Console, run the following and retain only statuses:

```javascript
console.log((await fetch('/api/auth/me')).status) // 200
console.log((await fetch('/api/auth/logout', { method: 'POST' })).status) // 204
console.log((await fetch('/api/auth/me')).status) // 401
```

5. Open the login URL again, sign in and confirm `/auth/me` returns 200. Verify
   existing Library/preferences remain intact; the prepared database fingerprints
   allow a separate read-only comparison.
6. Share only statuses/error codes and whether cookie/identity checks passed.
   Do not share passwords, SteamID64, cookies, full callback URLs, screenshots of
   Steam credential entry, or exported authentication network traces.

Phase 2B below implements backend ownership checks. Phase 2C implements
authenticated identity, cache reset and login UI in the frontend. Existing CI
removal remains intentional; checks are local.

## Authenticated user operations — Phase 2B

Steam sessions are now authoritative for account-specific API access. The existing
paths and JSON response schemas are retained; no `/users/me` aliases are added.
An ID in a URL/body selects a target but grants no permission. Services receive the
verified session ID only after it matches the target.

| Endpoint | Before Phase 2B | Current access |
|---|---|---|
| `GET /users/{steamid64}/library` | Public, including preferences/playtime/sync metadata | Signed-in owner |
| `GET /users/{steamid64}/preferences` | Public | Signed-in owner |
| `POST /recommendations` | Public ID-based read | Signed-in owner matching body ID |
| `POST /users/{steamid64}/library/sync` | Development flag + test-account allowlist | Signed-in owner + trusted origin |
| `PUT /users/{steamid64}/preferences/{steam_app_id}` | Development flag + test-account allowlist | Signed-in owner + trusted origin |
| `DELETE /users/{steamid64}/preferences/{steam_app_id}` | Development flag + test-account allowlist | Signed-in owner + trusted origin |
| Search, games, explanations, health | Public | Public |
| Local importer/diagnostic CLI commands | Administrative environment/DB access | Unchanged administrative access |

There is no public version of the full Library or preference-derived recommendation
response. Anonymous, malformed, expired and revoked sessions receive **401**;
cross-account requests receive **403 / account_forbidden**, including unknown target
accounts. This check precedes private service queries, reservations, Steam and Qdrant.
An unavailable auth database or disabled authentication returns **503**. Authenticated
invalid path IDs/preferences/pagination/limits return **422**. Existing missing-game
and unowned-PUT errors remain **404**, with safe messages. Existing service dependency
errors remain mapped to their previous statuses.

### Ownership, CSRF and transactions

All six routes reuse `get_current_user` and the shared `require_account_owner`
check. Session resolution uses the existing hash-only, unexpired session/User join.
Its short read transaction ends before preference handlers open write transactions;
`expire_on_commit=False` preserves the loaded identity. No extra auth pool, session
system or schema is introduced.

Sync, PUT and DELETE reuse `require_trusted_origin`: an exact configured trusted
Origin is required, with Referer fallback only when Origin is absent. Missing,
null, duplicate or untrusted origins fail **403 / csrf_failed** before mutation.
Authentication is checked first, so anonymous writes return 401. CORS is unchanged
and is not the authorization/CSRF mechanism. The read-only recommendation POST
requires authentication/ownership but does not add a state-changing operation.

Protected responses, including handled errors, validation failures, preflight and
unexpected 500s, use `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.
Unexpected errors retain status 500; they are not relabelled dependency outages.

`MANUAL_LIBRARY_SYNC_ENABLED` and `USER_PREFERENCE_WRITES_ENABLED` have been retired
from settings, Compose and `.env.example`. Old private `.env` entries are ignored;
remove them when convenient. `STEAMID64_TEST` remains optional private diagnostic
configuration, not authentication. An authenticated owner needs neither allowlist
nor development mode. Both Library capability booleans are true in authorized
owner responses. `AUTH_ENABLED=false` fails closed instead of restoring the old policy.

Cooldowns, single-worker reservations, timeout limits, enrichment bounds, ownership
upserts, legacy neutral/unowned clearing and recommendation algorithms are unchanged.
No migrations or new dependencies are needed. Sync still supports only one API worker;
newly imported games can remain unindexed until the separate embedding workflow runs.

### Local verification and real-browser check

Local automated checks: **663 focused Python tests** and **123 isolated PostgreSQL
tests** pass. The database tests use UUID-isolated databases on a disposable test
server, never the populated development database. They include actual session lookup
followed by sync/preference commits, cross-account and CSRF preservation checks,
expired/revoked sessions, and persisted feedback through the real recommendation API
with only Qdrant mocked. Like/dislike changes controlled profile/affinity/ranking
signals; clearing restores baseline results. This proves wiring, not relevance quality.

**100 existing mocked browser regressions and four new assisted-helper safety
checks pass**. TypeScript, ESLint, production build, repository Ruff and affected
Python formatting checks pass. Through the real Vite proxy, all six anonymous
account operations returned 401 with privacy headers; public health/games/search
returned 200 (three search results). These readiness requests performed no sync,
preference mutation or explanation generation. CI remains intentionally removed.
The historical ID-only Python launcher now exits with status 2 and a session-required
message in every mode, before configuration reads, browser startup or recovery files.
It cannot impersonate an owner or perform unauthenticated cleanup.

The real-browser check remains **pending**. To perform it on your consenting account:

1. Open **http://localhost:5173/api/auth/steam/login**, complete sign-in, and privately
   confirm `/api/auth/me` matches your account. Use localhost consistently. Check
   `gamerec_session` is HttpOnly in DevTools; do not copy/export the cookie.
2. From **http://localhost:5173/discover**, run this in DevTools Console. It performs
   one real own-library sync, a temporary like then dislike, restores the original
   unrated state, checks persisted recommendations and authorization, then logs out.
   Keep the tab open and avoid concurrent preference edits during this check.

```javascript
const { verifyAuthenticatedAccount } = await import('/scripts/verify_authenticated_account.ts');
const accountCheck = await verifyAuthenticatedAccount({ sync: true });
console.log(accountCheck);
```

The helper is outside the production application entry point and adds no route or
login UI. It uses your browser session and derives the ID from `/auth/me`; neither
identity nor cookie is returned in its report. If the first 100 stored games contain
no unrated game, preference checks are explicitly skipped. The chosen game may be
unindexed or too weak to change final scores/order; unchanged results are valid
observations. Top-20 exports and per-game hybrid/affinity comparisons are actual
returned values, not inferred rankings.

Expected report: own reads succeed; sync timestamp is persisted; preference check
and restoration succeed; restored scores/order match; cross-account checks return
403; logout returns 204; logged-out protected requests return 401. A changed ranking
is not required, and no improved-relevance claim is implied.

If `recovery_app_id` is non-null, cleanup could not be confirmed: keep the signed-in
session, clear that game's temporary rating in your own Library and verify its saved
state before continuing. A lost sync response can still mean a committed import;
refresh stored data before retrying, and do not blindly rerun `sync: true`. Use
`{ sync: false }` for a later check that must not contact Steam. Redact identifiers
before retaining evidence; do not share cookies, callback URLs or auth network traces.

### Phase 2C compatibility work

The frontend changes listed at the Phase 2B checkpoint are implemented below.
Backend ownership checks, cookie policy, strict Origin/Referer CSRF checks and
all API schemas remain unchanged. No additional backend edits were needed.

## Steam login and account experience — Phase 2C

Use **Sign in with Steam** in the existing sidebar/mobile header. It performs a
normal top-level navigation through the configured API base (`/api` by default)
to `/auth/steam/login`. Steam credentials are entered only on Steam; FastAPI
verifies the callback and establishes its HttpOnly session. No frontend code
parses OpenID assertions or reads session cookies.

The compact signed-in control reads **Signed in with Steam** with **Sign out**.
No full Steam ID, invented profile name or avatar is displayed. Discovery,
search and on-demand explanations remain public.

### Current-user query and protected pages

`getCurrentUser(signal)` returns `{ steamid64: string }` or null for the expected
401. `useCurrentUser()` is the single identity source, keyed by `['current-user']`.
Loading and server/network errors are distinct from signed out. Authentication
has no automatic retries and is rechecked on initial load, protected-page entry,
window focus and reconnect. There is no polling. Authentication outages show an
explicit retry while public discovery remains usable.

Library and Recommendations derive their ID exclusively from that query. Both
sidebar links load the signed-in account automatically; manual Library ID inputs,
development selectors and router-state identity handoffs are removed. Normal
Library URLs use `/library?page=N`; only matching-owner legacy links redirect to
that canonical route. A different/invalid legacy ID displays an unavailable state
without requesting it. Protected pages hide private content while authentication
is checking, uncertain or signing out.

An empty stored Library offers **Sync Library** when permitted. Sign-in never
starts an import. Sync/Like/Dislike/Clear now respect owner capability flags in
production as well as development. Existing cooldowns, pending protections,
background-refresh snapshots, error recovery and ranking order remain intact.

All API fetches explicitly use `credentials: 'include'`. Browsers supply the
trusted Origin/Referer for writes; no new CSRF cookie, token, header or exemption
is introduced. 401 ends the private presentation; 403 distinguishes ownership
from `csrf_failed`. A configured ID never authenticates a caller.

### Login return, logout and cache privacy

Sign-in remembers only one of `/discover`, `/library` or `/recommendations`, with
a ten-minute expiry, in `sessionStorage['gamerec.auth.returnTo']`. It stores no ID,
query parameters or session material. After the backend returns to Discover and
a fresh `/auth/me` succeeds, this allowlisted destination is consumed once and
restored with history replacement. Invalid/expired entries are discarded. If
storage is unavailable, login still returns to Discover. The backend redirect
configuration and OpenID protections are unchanged.

Sign out calls the backend first. Pending logout blocks private interaction.
Success clears private query and mutation data, resets identity and returns to
Discover. Failure says sign-out could not be confirmed and rechecks the server;
a lost response can still follow a completed logout. No frontend-only logout is
presented as successful server revocation.

A per-QueryClient generation counter fences asynchronous work; it is not another
identity store. Logout attempts, confirmed identity changes and current-generation
401s advance it. Obsolete reads are cancelled, and private caches/mutation entries
are removed on successful logout, account changes and expiry. Late callbacks
cannot restore old data or expire a newer session, including A→B→A transitions.
Public discovery/explanation caches are retained. Already accepted server writes
may still complete after logout; logging in again reads current stored data.
Expiry/revocation is detected on revalidation or a protected response, not by a
background push notification.

### Verification and screenshots

On **13 November 2026**, **133 mocked browser tests**, **two production-preview
checks**, **391 backend auth/API regressions**, TypeScript, ESLint, production
build, repository Ruff and diff checks passed. No additional backend change or
migration was required. Tests cover native redirect navigation, credentials and
HttpOnly-cookie transport, Origin, safe return destinations, automatic sidebar
identity, first-time empty state, pagination, feedback, sync, logout uncertainty,
401/403 handling, account switching and delayed requests/mutations.

The real public browser regression passed with the existing model service running:
20 search results and two explicit explanations through Vite, with no automatic
explanation request. Ollama was initially stopped (the expected 503), temporarily
started without model downloads, and restored to its prior stopped state afterward.
The temporary production preview was also stopped. Explanations still require
Ollama to be running; ordinary search and personal pages do not.

The real proxy authentication-readiness check also passed without entering Steam
credentials: expected 401/403/204, Steam 303, protected temporary cookie attributes
and trusted/untrusted CORS decisions. Signed-in end-to-end UI verification remains
pending the assisted browser pass; mocked evidence is not a real Steam login.

Desktop/mobile signed-in and signed-out screenshots were captured and visually
reviewed under ignored `frontend/live-results/auth/phase2c/`. Keyboard focus,
reduced motion and no horizontal overflow were checked at 320, 390, 768 and 1440 px.
Screenshots use synthetic games/accounts and display no account identifier.

Run normal frontend commands inside the existing frontend container:

```bash
docker compose exec -T frontend npm run typecheck
docker compose exec -T frontend npm run lint
docker compose exec -T frontend npm run build
# Use the existing matched Playwright container command from Frontend checks:
# npm test                     -- mocked suite
# npm run test:production      -- built preview on frontend:5174, mocked API
# npm run test:auth:live       -- real proxy, no Steam credential entry
```

The preview check needs `docker compose exec -d frontend npm run preview -- --port 5174`
while it runs; it is temporary local verification, not deployment infrastructure.
Keep live traces/video/automatic screenshots disabled and retain redacted evidence.

### Assisted Steam browser pass

The running development frontend hot-reloads these changes; a rebuild is not
needed to see the sign-in control. Use localhost:5173 consistently. Do not export
cookies, raw auth traces or full callback URLs.

1. Use **Sign out** if already signed in. Confirm protected pages show Sign in to
   continue, and public Discover/search still work.
2. From Library click **Sign in with Steam**, complete Steam login, and confirm
   you return to Library with your own data and no ID entry. Privately verify the
   `/auth/me` identity if needed. Check the session cookie is HttpOnly in DevTools.
3. Click **Sync Library** once, wait for completion and confirm the stored timestamp.
   If the response is lost, refresh stored data before retrying; respect cooldown.
   OpenID login does not grant visibility into private Steam game details.
4. Choose an originally unrated owned game, click Like, open Recommendations using
   the sidebar, then return to Library and verify the rating persisted. Click the
   selected Like to clear it and verify Unrated. Restore before signing out.
5. Sign out, visit both protected pages, and refresh. Private games should be gone;
   public discovery remains usable. For a separate revocation check, sign out in a
   second tab, return to the original tab and confirm revalidation hides private data.

Report only completed steps and safe statuses. Existing recommendation relevance
and explanation-quality ratings remain pending. No algorithms, ingestion policy,
new profile APIs or dependencies were added. CI stays intentionally removed.

## Natural-language game search

Start the API, the existing embeddings service, PostgreSQL, and Qdrant with a
configured local `.env`:

```bash
docker compose --profile embeddings up -d --build api embeddings qdrant
```

The API requests a query vector from the embeddings container, which loads the
existing pinned model once per worker. The API container does not install or
load SentenceTransformers. The embeddings service must be running for search;
its internal URL defaults to `http://embeddings:8100` and can be set with
`EMBEDDING_SERVICE_URL`. Starting the embeddings profile serves queries; it
does not regenerate game embeddings. A first build or model-cache miss may take
longer than subsequent starts.

`GET /search/games` accepts required `query`, optional `limit` (default 20,
range 1–100), repeated `genres` and `categories`, `release_year_from`,
`release_year_to` (years 1–9999), and `min_reviews` (non-negative). Multiple
values within a genre or category group match any value; supplied groups and
other constraints must all match. Labels are matched case-insensitively. The
default search ranking also requires 100 reviews before explicit filters are
applied. Invalid inputs return HTTP 422, and an unavailable dependency returns
HTTP 503.

```bash
curl -G 'http://localhost:8000/search/games' \
  --data-urlencode 'query=Cooperative survival crafting game with challenging bosses' \
  --data-urlencode 'limit=10'

curl -G 'http://localhost:8000/search/games' \
  --data-urlencode 'query=Cooperative survival crafting game with challenging bosses' \
  --data-urlencode 'genres=Action' --data-urlencode 'genres=RPG' \
  --data-urlencode 'categories=Co-op' \
  --data-urlencode 'release_year_from=2020' \
  --data-urlencode 'min_reviews=500'
```

The response has `query`, `limit`, `total`, and `games`. Each game has
`steam_app_id`, `name`, `similarity_score`, `popularity_score`,
`review_quality`, and `hybrid_score`, in descending hybrid-score order.
`total` counts returned games, not all catalogue matches. Search retrieves at
most 1,000 Qdrant candidates before metadata eligibility and filters, so
filtered searches can omit matching games outside that pool. Only games in the
current indexed Qdrant catalogue can be retrieved. Scores alone do not prove
that a result is relevant.

### Search evaluation and HTTP latency

With the services running, export a fixed 12-query, top-10 search benchmark and
measure three representative queries through the FastAPI endpoint. Each query
gets two warm-up requests followed by 20 sequential measured requests. The
script uses `time.perf_counter()` and reports successful-request median,
nearest-rank p95, minimum, maximum, and error count. Warm-ups and model startup
are excluded from latency statistics.

```bash
docker compose exec -T api python -m gamerec.scripts.evaluate_search \
  --base-url http://127.0.0.1:8000
```

The timestamped JSON output is written under `benchmark_results/search/`.
Queries and their order are fixed. Each result includes a nullable
`relevance_grade`; these are **not** generated ratings. To supply manual grades,
create a separate JSON file, for example:

```json
{
  "open_world_rpg": {"1091500": 2, "292030": 1, "489830": 0},
  "survival_crafting": {"892970": 2}
}
```

Then rerun the Compose command with `--labels path/to/labels.json` (a file under
the repository, visible in the API container).
Grades are 0 (irrelevant), 1 (partially relevant), or 2 (strongly relevant).
Precision@10 counts grades **1 or 2** as relevant. nDCG@10 and average graded
relevance@10 reuse the existing recommendation evaluation metrics. All three
metrics remain null until all ten returned games for a query have manual
grades; the output reports labelled count, coverage, missing app IDs, and
unfilled top-10 slots. A short result set is therefore not silently scored as
fully evaluated. Labels for games outside the current top ten are ignored; the
nDCG ideal ordering uses the judged top-ten games. Keep the query set and
judged pool consistent when comparing runs.

### Phase 9 verification snapshot (9 October 2026)

- Warm HTTP search latency was approximately **73–75 ms median** across the
  three measured queries. This was measured from inside the API container, with
  two warm-ups and 20 sequential requests per query; it excludes model startup
  and host-network latency.
- The evaluation exported **12 queries and 120 top-ten results**. Manual
  relevance labels are pending (0 of 120 results labelled), so Precision@10,
  nDCG@10, and average graded relevance@10 have not been calculated.
- Broad queries and queries with several constraints sometimes return games
  that appear to match only part of the intent. This is an inspection note,
  not a measured relevance result.
- Possible future improvements are richer game-text embeddings, combined
  lexical and vector retrieval, and better query understanding. These have not
  been implemented or evaluated.

## Local search explanations (Phase 10B)

The explanation service consumes the Phase 10A PostgreSQL context and calls
Ollama's native `/api/chat` endpoint. It uses a shared async HTTP client, requests
non-streaming JSON with the Pydantic schema, and validates the returned content.
It returns `matching_features` and `explanation`, alongside
separate Ollama timing/token metadata for inspection. Schema validation checks
format; it does not prove factual correctness.

### On-demand explanation API (Phase 10D)

`POST /explanations/search` reads the selected game's stored PostgreSQL metadata
and calls the existing explanation service. Ordinary `GET /search/games`
requests do not generate explanations.

```bash
curl -X POST http://localhost:8000/explanations/search \
  -H 'Content-Type: application/json' \
  -d '{"steam_app_id":108600,"search_query":"Cooperative survival crafting game with challenging bosses"}'
```

The JSON body requires a positive integer `steam_app_id` and a nonblank string
`search_query`, with at most 500 characters after trimming surrounding
whitespace. Numeric strings, booleans and fractional IDs are rejected.
The response contains only `steam_app_id`, `game_name`, `explanation` and
`matching_features`; prompts, metadata and inference diagnostics are not
returned. The `X-Explanation-Cache` header reports `HIT`, `MISS` or `BYPASS`.
Games without description, genres or categories receive the existing
neutral fallback with no model request.

| Condition | HTTP status |
|---|---:|
| Invalid request | 422 |
| Game not stored in PostgreSQL | 404 |
| Database or Ollama unavailable, including an uninstalled model | 503 |
| Ollama connection or inference timeout | 504 |
| Malformed or invalid model output | 502 |
| Unexpected internal failure | 500 |

Ollama is optional at API startup. Start it with the existing `llm` profile
below. Uncached explanations return 503 when Ollama is stopped; matching Redis
entries remain available while PostgreSQL is running. Ordinary search remains
available when its own dependencies are running. Explanation requests perform
no PostgreSQL writes. Partial matches are allowed, and reasonable inference and established
model knowledge remain permitted. Generated claims can still be wrong; the
Phase 10C evaluation does not establish universal factual accuracy.

Configure `OLLAMA_BASE_URL` (default `http://ollama:11434`), `OLLAMA_MODEL`
(default `qwen3:4b-instruct`), `OLLAMA_CONNECT_TIMEOUT` (5 seconds), and
`OLLAMA_GENERATION_TIMEOUT` (180 seconds) in the existing `.env` when needed.
Containers use the `ollama` hostname; host requests use `localhost`. The service
is under the `llm` profile and publishes port 11434 only on loopback.

Start on CPU, or opt into the NVIDIA reservation with the additional file:

```bash
# CPU-compatible default
docker compose --profile llm up -d ollama

# NVIDIA GPU, with Docker/driver support
docker compose -f compose.yml -f compose.ollama-gpu.yml --profile llm up -d ollama
```

Pull the model once, inspect it, and make a simple non-streaming chat request:

```bash
docker compose exec -T ollama ollama pull qwen3:4b-instruct
docker compose exec -T ollama ollama list

curl http://localhost:11434/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:4b-instruct","messages":[{"role":"user","content":"Say hello in one sentence."}],"stream":false}'
```

Models live in the named `ollama_models` volume mounted at `/root/.ollama`.
Container recreation keeps them; the startup command does not pull models.
The official image is `ollama/ollama:latest`, so record the image version/digest
when comparing results across updates. Local cloud features are disabled.

Verify GPU use after inference rather than relying on the reservation alone:

```bash
# WSL hardware visibility
/usr/lib/wsl/lib/nvidia-smi -L
# Loaded model residency: PROCESSOR should report GPU or a CPU/GPU split
docker compose exec -T ollama ollama ps
# GPU activity during generation
/usr/lib/wsl/lib/nvidia-smi --query-gpu=name,utilization.gpu,memory.used --format=csv
docker compose logs --tail 40 ollama
```

To switch to CPU, recreate only Ollama with the base file; model storage remains:

```bash
docker compose -f compose.yml --profile llm up -d --force-recreate ollama
```

Verify actual stored metadata for Project Zomboid, Cyberpunk 2077, and Quake:

```bash
docker compose up -d api
docker compose exec -T api python -m gamerec.scripts.verify_search_explanations
```

The script shares one client across the examples, prints metadata retrieval
and generation latency separately, and reports Ollama token counts and
durations (nanoseconds). If a preferred game lacks descriptive evidence, it
prints its substitution: Valheim, The Witcher 3, or DOOM respectively. Generated
text is printed and is not saved to PostgreSQL. Insufficient contexts get a
fixed response without an Ollama request. Network, timeout, missing-model, and
invalid-output failures raise explicit errors without retries or invented
responses.

The prompt explains why an already selected game may interest the user, targeting
1–2 natural sentences of approximately 30–60 words. Steam metadata is preferred,
with reasonable gameplay inference and well-established game knowledge allowed.
Model knowledge is not independently verified and must not be attributed to Steam
or PostgreSQL when the supplied context does not contain it. Uncertain specifics
should be omitted or qualified, especially for unfamiliar titles. Explicit
metadata contradictions take precedence; a
single-player category alone does not establish that multiplayer is absent.
The response has no limitations field or separate evidence classifications.
The query is supplied separately from the quoted metadata block, which is treated
as untrusted data. Model claims still need manual inspection; prompt instructions
and structured JSON are not a factual verification mechanism. CPU inference
can be slower, and the first inference may include model loading. Description
formatting retains Phase 10A's 1,000-character cap.

Verified on 9 October 2026 with Ollama 0.40.2 and model ID `0edcdef34593`:
the model survived container recreation, and `ollama ps` reported 100% GPU
residency on an RTX 4080. The three real-metadata examples took approximately
0.95–1.20 seconds per warm generation. An initial cold request took 36.8 seconds,
including about 21 seconds of model loading. These are individual verification
requests, not a latency benchmark or proof of grounding across other queries.

The recommendation-focused refinement removed the required limitations field
after confirming that it had no public API consumers. Strict JSON validation,
the no-request insufficient-information fallback, and existing Ollama error
handling are preserved. Automated tests verify these contracts; they do not
establish the truth of model-generated claims. The JSON schema cannot enforce
semantic grounding, inference qualification or prose length.

The Phase 10B recommendation prompt was compared with the previous style on identical
saved contexts for the three examples, using the same model and settings. Each
had two sentences and roughly 30–60 words; individual warm requests took
0.49–0.63 seconds versus 0.85–1.10 seconds previously, excluding warm-up.
Zomboid's output focused on co-op and crafting without a boss disclaimer.
Cyberpunk described narrative depth, customisation and player agency,
and Quake asserted pacing and intensity not present in the saved metadata. These
were failures of the earlier metadata-only policy, not independently established
factual errors: absence from a short description is not proof of hallucination.
This small comparison does not establish a reliable overall quality improvement.

## Explanation evaluation (Phase 10C)

The tracked `tests/data/explanation_evaluation_cases.json` contains 18 real
catalogue game/query pairs: five well-known games, four lower-review selections
used as a familiarity proxy, three partial matches, three deliberate mismatches,
and three sparse-metadata cases. Review counts do not establish what the model
knows. Cases include their selection reasons and no invented descriptions.

Run the revised policy, optionally comparing the exact saved Phase 10B prompt:

```bash
docker compose exec -T api python -m gamerec.scripts.evaluate_explanations \
  --compare-prompt tests/data/explanation_prompt_phase10b.json
```

The runner reads PostgreSQL once per case, closes the session before inference,
and shares one async Ollama client. It saves `contexts_<timestamp>.json` before
generation and `evaluation_<timestamp>.json` afterwards under the existing
Git-ignored `benchmark_results/explanations/` directory. Each report contains
the contexts, exact formatted user input, prompts/schema descriptions, outputs,
errors, token counts and timings. Both policies use identical contexts, model
and options; the previous schema's descriptions are retained with its prompt.
The two-field response contract is unchanged. One warm-up per version is excluded
by default; successful model latency summaries exclude fallbacks and errors.
There is one measured generation per case/version, suitable for qualitative
inspection rather than statistical evidence of improvement. At most 20 cases
can be loaded; generation never runs over the catalogue automatically.

Reuse a saved context snapshot without querying PostgreSQL:

```bash
docker compose exec -T api python -m gamerec.scripts.evaluate_explanations \
  --snapshot benchmark_results/explanations/contexts_<timestamp>.json \
  --compare-prompt tests/data/explanation_prompt_phase10b.json
```

The evaluation JSON doubles as the human review template. Each generation has
blank `human_review` fields. Rate factual accuracy, query relevance, usefulness,
and naturalness from 1 (poor) to 5 (strong). Set `hallucination_detected` to
`true`, `false`, or `"undetermined"`, with optional notes; leave unreviewed fields
as `null`. Accuracy concerns the actual game, not verbatim metadata overlap.
Use independent knowledge or verification where needed; if a claim cannot be
checked, leave its accuracy score blank and mark the verdict undetermined.
The generating model never supplies reviewer ratings or factuality verdicts.

After editing the report, validate and summarize only the supplied ratings:

```bash
docker compose exec -T api python -m gamerec.scripts.evaluate_explanations \
  --review-file benchmark_results/explanations/evaluation_<timestamp>.json
```

Review mode performs no metadata retrieval or inference. Means remain `null`
without ratings; each dimension reports its rated count, and verdict coverage
distinguishes pending reviews from undetermined judgments. This phase adds no
claim validator, model judge, external retrieval, API endpoint or ranking change.

References: [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs),
[chat API](https://docs.ollama.com/api/chat), and
[Compose GPU reservations](https://docs.docker.com/compose/how-tos/gpu-support/).

### Explanation reliability and performance (Phase 10E)

Benchmark the actual FastAPI endpoint with the existing Zomboid, Cyberpunk,
Quake, Terraria and Tetrapulse evaluation cases:

```bash
docker compose exec -T api python -m gamerec.scripts.benchmark_explanations \
  --base-url http://127.0.0.1:8000 --repetitions 4 \
  --concurrency 1 2 5 --warmups 2 --timeout 240
```

Repetitions are **per case**: defaults produce 20 measured requests at each
concurrency level and two excluded sequential warm-ups before each level.
`--concurrency 2` measures just that level. `--output path/to/report.json`
selects a new report path; otherwise a timestamped JSON file is written under
`benchmark_results/explanations/`. Existing files are not overwritten.

With Phase 11 caching enabled, the default benchmark records a mixture of
cache outcomes. Use the Phase 11 baseline procedure below to measure uncached
generation and inspect `cache_outcomes` when interpreting results.

The report records request IDs, status, latency and error category; successful
and failed latency statistics are separate. It includes mean, median,
nearest-rank p95, minimum, maximum, timeout/error counts and throughput over the
complete measured batch. Request latency starts after acquiring the benchmark
semaphore; offsets and peak in-flight counts document overlap. Failed warm-ups
are retained and flagged. Empty-success latency statistics are `null`.

The public API provides no inference diagnostics. For separate native service
timings, reuse `verify_search_explanations`; its Ollama durations are nanoseconds,
so divide by 1,000,000 for milliseconds. These are separate requests and cannot
be subtracted from HTTP measurements to reconstruct exact API overhead.

For a manual cold-model measurement, send an empty
[generate request](https://docs.ollama.com/api/generate) with
`{"model":"qwen3:4b-instruct","keep_alive":0,"stream":false}` to Ollama,
then verify that the model is absent from
[`GET /api/ps`](https://docs.ollama.com/api/ps) before timing one explanation
API request. Keep this request separate from the warm benchmark. This measures
an unloaded model in memory; filesystem caches are not cleared.

The local Phase 10E run used an RTX 4080 with verified GPU residency. Across
20 requests per level, warm median/p95 HTTP latency was approximately
637/811 ms at concurrency 1, 1,217/1,442 ms at 2 and 3,144/3,231 ms at 5.
Throughput stayed near 1.5–1.6 requests/second; all 60 requests succeeded.
The verified cold-model request took 4.84 seconds. See
`benchmark_results/explanations/phase10e_report.md` for environment, separate
internal timings and outage/recovery evidence.

These are small local measurements dependent on hardware, model size and
system load, not evidence of production scalability. Phase 10C factuality and
usefulness reviews remain separate and pending. No queues, concurrency limits,
caching, prompt optimisation or ranking changes were introduced.

## Explanation caching (Phase 11)

Compose enables a disposable Redis cache for successful explanation responses.
Start it with the API and use the normal explanation endpoint:

```bash
docker compose up -d --build api redis
```

`REDIS_URL` defaults to `redis://redis:6379/0`. The service has no published
host port, no persistence, a 128 MiB container limit and a 96 MiB Redis data
budget with LRU eviction; the remaining memory allows for server overhead.
The API owns one async Redis pool and closes it on shutdown. Startup does not
connect to Redis. Connection and command timeouts default to 100 ms, with no
automatic retries.

| Setting | Default |
|---|---|
| `EXPLANATION_CACHE_ENABLED` | `true` in Compose; `false` outside Compose |
| `EXPLANATION_CACHE_TTL_SECONDS` | `3600` |
| `EXPLANATION_CACHE_VERSION` | `v1` |
| `REDIS_TIMEOUT_SECONDS` | `0.1` |

Every request first loads the current PostgreSQL context. The key hashes the
normalized query/context, exact prompt, output schema, model/server identity
and generation options. Whitespace is normalized; query case is preserved.
Changes select a fresh key automatically. Bump `EXPLANATION_CACHE_VERSION`
when replacing model weights under the same model tag. Old entries expire
after the TTL or are evicted under memory pressure.

Redis values contain only the validated `explanation` and `matching_features`.
Errors and insufficient-information fallbacks are not cached. Corrupt entries
are treated as misses. Redis read failures bypass the cache; write failures
still return the generated explanation. Header values mean:

- `HIT`: a valid cached explanation was returned without an Ollama request.
- `MISS`: a cache lookup required generation; a failed cache write does not
  change the successful API response.
- `BYPASS`: caching is disabled, the context needs the fallback, or Redis could
  not be read. No cache write is attempted after a read failure.

For a fresh comparison, choose a new benchmark namespace and keep Ollama running:

```bash
phase11_cache_version="bench-$(date +%s)"
EXPLANATION_CACHE_ENABLED=false EXPLANATION_CACHE_VERSION="$phase11_cache_version" \
  docker compose up -d --no-deps api
docker compose exec -T api python -m gamerec.scripts.benchmark_explanations \
  --scenario baseline --base-url http://127.0.0.1:8000

EXPLANATION_CACHE_ENABLED=true EXPLANATION_CACHE_VERSION="$phase11_cache_version" \
  docker compose up -d --no-deps api
docker compose exec -T api python -m gamerec.scripts.benchmark_explanations \
  --scenario miss --repetitions 1 --concurrency 1 --warmups 0 \
  --base-url http://127.0.0.1:8000
docker compose exec -T api python -m gamerec.scripts.benchmark_explanations \
  --scenario hit --base-url http://127.0.0.1:8000

# Restore the normal configuration; benchmark entries expire automatically.
docker compose up -d --no-deps api
```

Scenarios verify response headers and count mismatches as failures. `hit`
preloads all five cases outside measurements. Defaults measure 20 requests at
each concurrency level (1, 2, 5); `miss` makes one measured pass over five cases.
Use `--output` for a new report path, or accept timestamped JSON output.
Only delete keys belonging to your isolated benchmark namespace; never use
`FLUSHDB` or `FLUSHALL` against the shared cache.

Local results on **12 November 2026**, RTX 4080 with GPU residency verified:

| Concurrency | Uncached median/p95 | Cache-hit median/p95 | Measured requests per group |
|---|---:|---:|---:|
| 1 | 627.9 / 803.8 ms | 2.7 / 3.3 ms | 20 |
| 2 | 1,210.0 / 1,435.8 ms | 4.0 / 20.2 ms | 20 |
| 5 | 3,182.9 / 3,218.8 ms | 14.6 / 39.1 ms | 20 |

All 60 baseline, five miss and 60 hit requests succeeded. The five misses had
a 629.6 ms median; their 1,118.9 ms p95 is the maximum of a very small sample.
These are local HTTP timings from inside the API container. The short hit
batches are sensitive to scheduling and database connection-pool warm-up and
do not establish production throughput. Concurrent identical misses can still
generate duplicate explanations; cache hits are safe under concurrent requests.
Human explanation-quality ratings remain pending.

Real Redis expiry, Redis outage/startup/recovery, cached responses during an
Ollama outage, uncached 503s and model recovery were verified. Search and health
remained available. The local report is
`benchmark_results/explanations/phase11_report.md`; raw results follow the
existing ignored-results convention.

The **Focused checks** GitHub Actions workflow was intentionally removed at the
project owner's request after GitHub prevented the job from starting because of
an account billing restriction. Phase 11 was verified locally; no successful
GitHub CI run is claimed. Continue running focused pytest and Ruff checks
locally while hosted CI is disabled.

## Recommendation evaluation

With the API, PostgreSQL and Qdrant services running, evaluate the configured
test user using the current catalogue and library. The script only reads these
services and writes a new JSON file under `benchmark_results/recommendations/`.
That directory is ignored by Git because results may contain personal taste data.

```bash
docker compose exec api python -m gamerec.scripts.evaluate_recommendations --runs 20 --warmups 2
```

Add `--mmr` to compare the current hybrid top 20 with MMR at lambda 0.85 using
the same ranked candidate pool. Use `--mmr-lambda 0.75` for another ablation.
For a quick local check, use `--runs 1 --warmups 0`. The default run reports
median, nearest-rank p95, minimum and maximum latency across 20 measured runs.
Each configuration times profile construction through ranking. MMR fetch and
selection time is recorded separately and included in its total.

Manual labels are optional. A JSON file with `{"730": 2, "105600": 1}` gives
grades 0 (poor), 1 (somewhat relevant), or 2 (would consider playing). Supply
it with `--labels /app/path/to/labels.json`. For multiple users, repeat
`--steamid64` and use `{"users": {"<user_id_hash>": {"730": 2}}}`. The user hash
appears in the JSON output; raw Steam IDs are not saved. Missing labels leave
relevance and nDCG metrics null and report coverage explicitly. The nDCG ideal
ranking uses all grades in the supplied label file, so keep the judged pool
consistent across comparisons.

Configurations A and B use profile-only retrieval; C uses the current profile
and seed retrieval. All use the same review eligibility threshold and top-K.
A ranks by semantic similarity; B uses the historical 0.70/0.15/0.15
semantic/popularity/review preset; C uses the live `rank_candidates` defaults.
Differences between A and C combine retrieval and scoring changes. Pairwise
embedding cosine is a content-diversity diagnostic, not a relevance measure.

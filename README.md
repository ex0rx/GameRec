# GameRec

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

Invalid inputs return 422, an unknown stored user returns 404, and unavailable
PostgreSQL/Qdrant dependencies return a safe 503. Existing users with no usable
profile or eligible candidates receive 200 with an empty `games` list. This
does **not** prove that their Steam library is empty. Authentication is deferred;
supplying an ID does not verify account ownership or grant access control.

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
recommendation client/hook tests. The live check separately reuses a configured
`STEAMID64_TEST` from the API container. Set it only in the existing local backend
`.env`; do not add a personal account ID to source, command examples, committed
fixtures, or `VITE_*` variables. The account's library and embeddings must already
be stored; this check does not import or refresh them.

From WSL at the repository root, with Docker running:

```bash
docker compose --profile frontend up -d --build api frontend qdrant
python3 frontend/scripts/verify_recommendations.py
```

If running an API container across source changes, restart it to register the
new route (`docker compose restart api`). The launcher supports `--project-name`
for a non-default Compose project and `--base-url` for a different frontend URL.
It captures the ID without printing it, passes only that ID to the browser
test environment and redacts it from subprocess diagnostics. Live identifiers
stay in memory, out of harness inputs. Traces, screenshots and video are disabled
for this check. Only redacted status/count evidence is saved under
`frontend/live-results/recommendations/`; the directory is ignored by Git.

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

In Vite **development mode only**, expand **Development profile**, enter a
canonical positive unsigned-64-bit Steam ID and select **Load recommendations**.
This selects an already stored profile for testing; it is not sign-in or proof
of account ownership. The field is masked and clears on submission. The selected
ID stays in page memory and the existing in-memory query key, never in URLs,
local/session storage or `VITE_*` settings. Leaving the page clears its selection;
**Clear profile** also hides results. Production builds omit the selector and
show a no-profile placeholder on direct visits. Phase 4 also supports an explicit
Library-to-Recommendations handoff in both development and production builds.
Invalid or missing IDs make no recommendation request.

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
python3 frontend/scripts/verify_recommendations.py --page
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
Enter a SteamID64 to view a library already stored in GameRec. This reads
PostgreSQL; it does not sign in, verify ownership, import or synchronise Steam
data. The input and routes are available in development and production builds.

```bash
# Uses the existing configured backend .env and stored database.
docker compose --profile frontend up -d --build api frontend
```

The routes are `/library` and `/library/:steamid64`, with optional `?page=2`.
IDs are trimmed and must contain exactly 17 ASCII decimal digits. They remain
strings; no request runs for missing or invalid IDs. The selected ID intentionally
appears in the shareable URL, browser history and potentially normal access logs.
It is not proof of account ownership. No additional local/session storage is used.
Switching IDs resets pagination; reload and Back/Forward restore the URL state.

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
python3 frontend/scripts/verify_recommendations.py --library
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
made. The existing API exposes libraries by supplied ID without ownership checks;
this is a local prototype, not an authenticated multi-user deployment.

## Manual Steam library synchronisation — Frontend Phase 5

**Sync Library** is a development-only, explicitly requested refresh of the one
configured test account. Enable it in the backend's local `.env`:

```dotenv
ENVIRONMENT=development
MANUAL_LIBRARY_SYNC_ENABLED=true
# Set STEAMID64_TEST privately to the consenting account's 17-digit Steam ID.
MANUAL_LIBRARY_SYNC_COOLDOWN_SECONDS=60
```

Recreate the API to load changed settings:

```bash
docker compose up -d --no-deps api
```

Enablement defaults to false. The server requires all three checks: development
environment, enabled flag and exact match to a valid configured `STEAMID64_TEST`.
Missing/invalid configuration fails closed. `GET /users/{steamid64}/library` adds
`sync_allowed`; the control appears only in Vite development mode when that value
is true. A production build omits the action even when a server permits it.
This is a local development restriction, not authentication or ownership proof.
Steam must make the account's game details visible to the API; unavailable data
is not an empty library. See the [official GetOwnedGames documentation](https://partner.steamgames.com/doc/webapi/IPlayerService#GetOwnedGames).

### Sync contract and behaviour

`POST /users/{steamid64}/library/sync` accepts an exact 17-ASCII-digit string path
identifier and no request body. It may create the permitted user if not yet stored.
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
python3 frontend/scripts/verify_recommendations.py --library-sync
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

Set these in the private backend `.env`, using the existing consenting test
account (never commit its ID):

```dotenv
ENVIRONMENT=development
USER_PREFERENCE_WRITES_ENABLED=true
# STEAMID64_TEST must already contain the exact permitted 17-digit account ID.
```

```bash
docker compose up -d --no-deps api
```

Refresh the development Library page and load that account. Like and Dislike
buttons appear only when `import.meta.env.DEV` and the response's
`preference_writes_allowed` are true. Steam synchronisation does not need to be
enabled. Other libraries and production builds show read-only preference labels.
These local restrictions do not authenticate an account owner.

### Saved preference contract and UI

- `PUT /users/{steamid64}/preferences/{steam_app_id}` accepts
  `{"preference":"liked"}` or `"disliked"`; legacy `"neutral"` remains accepted.
  The existing response contains identity, preference and update timestamp.
- `DELETE` at the same URL returns **204** and removes the row, restoring unrated
  behaviour. It is idempotent. `null` is a Library response state, not a PUT value.
- New/updated ratings require stored ownership. Existing unowned legacy records
  remain readable through GET preferences and clearable through DELETE.
- Write IDs must be exactly 17 ASCII digits. Disallowed writes return **403**
  (`preference_forbidden`); unknown user/game or unowned PUT target returns **404**
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
python3 frontend/scripts/verify_recommendations.py --preferences
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
The API returned to its normal configuration: preference writes default to off;
the user's existing manual-sync setting was preserved. Use the enablement section
above to show the preference controls.

Focused commands, alongside the existing frontend checks/browser command:

```bash
uv run pytest -q tests/test_preferences_api.py tests/test_preference_feedback.py tests/test_verify_recommendations_frontend.py
# Only with TEST_POSTGRES_ADMIN_URL set to a dedicated /gamerec_test_admin DB:
uv run pytest -q --run-integration tests/integration/test_preferences.py tests/integration/test_preference_recommendations.py tests/integration/test_library_api.py
```

Limitations: one configured development account, unauthenticated read-only browsing,
limited indexed catalogue/metadata and no promise that one rating changes a specific
rank. Only-dislike histories still return no usable positive recommendation profile.
No manual relevance or explanation-quality labels were added. The shared floor is
proved with deterministic zero-playtime fixtures; the live chosen games had nonzero
playtime. Existing best-effort artwork limitations remain.

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

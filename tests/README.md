# Running the tests

Use the project's installed Python 3.13 environment (`uv sync --dev` when setting
up a new checkout). Run from the repository root.

## Fast suite: no PostgreSQL required

```bash
.venv/bin/python -m pytest -p no:cacheprovider tests -q
```

The existing mocked tests run by default. PostgreSQL integration tests are
explicitly skipped unless `--run-integration` is passed. Test configuration uses
synthetic application settings, never the configured development database.
Unmocked HTTPX network requests fail immediately; Steam must use `MockTransport`.
HTTP API tests use an in-process ASGI transport.

## Full suite with disposable PostgreSQL 17

Start a dedicated test container with temporary storage and no development
volumes. These credentials are synthetic and only for this local test container.

```bash
docker run --rm -d --name gamerec-phase4f-tests \
  --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=gamerec_test \
  -e POSTGRES_PASSWORD=synthetic-test-password \
  -e POSTGRES_DB=gamerec_test_admin \
  -p 127.0.0.1:55439:5432 postgres:17

docker exec gamerec-phase4f-tests \
  pg_isready -U gamerec_test -d gamerec_test_admin
```

Wait for `pg_isready` to report that connections are accepted before testing.
If the port is occupied, choose another loopback port and update the URL below.
Do not point this URL at the development or production server.

```bash
export TEST_POSTGRES_ADMIN_URL='postgresql+asyncpg://gamerec_test:synthetic-test-password@127.0.0.1:55439/gamerec_test_admin'

# Only persistence/integration coverage:
.venv/bin/python -m pytest -p no:cacheprovider --run-integration tests/integration -q

# All unit, HTTP, and PostgreSQL tests:
.venv/bin/python -m pytest -p no:cacheprovider --run-integration tests -q

docker stop gamerec-phase4f-tests
unset TEST_POSTGRES_ADMIN_URL
```

`--run-integration` fails if the URL is missing or the server is unavailable;
it does not silently skip requested database verification. For CI, provide a
dedicated PostgreSQL service with an initial database named `gamerec_test_admin`
and a role with `CREATEDB`, then run the same full-suite command. No Docker calls
are made from pytest itself.

Each integration test creates a UUID-named database, uses real commits and fresh
verification sessions, then closes connections and drops only that database.
API tests override only the database dependency; routes and services run normally.
The fixture builds tables from model metadata. It does **not** verify migration
upgrades, preserve data across historical migrations, or access live Steam.
An interrupted pytest process may leave a test database in the disposable
container; stopping the container removes all its temporary storage.

## Phase 4 coverage

- Existing unit tests: Steam parsing/privacy, optional fields, malformed responses,
  retries, pacing/concurrency, targeted enrichment SQL, API validation/serialization.
- `integration/test_library_sync.py`: first/repeated sync, both playtime columns,
  catalogue preservation, helper transaction ownership, validation and late SQL
  error rollback, empty versus inaccessible responses, enrichment failure after
  successful library persistence.
- `integration/test_library_api.py`: actual SQL joins, stable ordering and page
  boundaries, shared-game user isolation, unknown/empty users, nullable playtime
  and timestamps.
- `integration/test_preferences.py`: real PUT/GET/DELETE, allowed/invalid values,
  missing users/games, empty/ordered paginated results, repeated deletion,
  composite-key uniqueness, isolation, ownership/playtime preservation and resync
  preservation of explicit preferences.

## Static checks

```bash
.venv/bin/ruff check --no-cache .
.venv/bin/ruff format --no-cache --check .
```

Mypy is not currently configured in the project.

## Phase 5F embedding coverage

Install the optional inference dependency along with the test tools when setting
up a checkout: `uv sync --dev --group embeddings`. Tests use a deterministic,
text-dependent fake encoder with 384-dimensional normalized vectors. They never
construct a real model or download weights; Hugging Face offline settings are
also enabled. Embedding model settings are synthetic test values.

Using the disposable PostgreSQL server described above, run the focused suite:

```bash
.venv/bin/python -m pytest -p no:cacheprovider --run-integration \
  tests/test_game_text.py tests/test_similarity.py tests/test_game_embeddings.py \
  tests/integration/test_game_embeddings.py -q
```

- Existing text tests cover missing/blank metadata and reproducibility.
- Unit tests cover cosine scores, empty/mismatched/zero vectors, empty generation
  and persistence, invalid dimensions, missing hashes, inference result-count
  mismatches, and pipeline limit validation.
- Database tests cover new/unchanged/modified games, hashes and app-ID mapping,
  model/revision changes, insert/upsert and retrieval, revision isolation,
  eligibility and cursor pagination, batch and total limits, repeat-run counters,
  transaction rollback and rerun recovery, and similarity ranking and filtering.
- `max_games` counts eligible games visited, including unchanged games. Recovery
  starts scanning from the beginning and skips matching hashes; there is no
  persistent cursor or automatic retry within a failed run.

Known behavior outside this slice: cosine similarity does not reject non-finite
components; negative `top_k` uses Python slicing rather than validation; ties
have no explicit secondary ordering. SQL eligibility trims ordinary spaces,
not all whitespace. The generation helper itself does not validate vector
width (persistence does), and the database has no array-width constraint. Tests
validate application transactions and model metadata, not migration upgrades or
semantic quality of a real embedding model.

## Phase 6C PostgreSQL to Qdrant sync

Using the dedicated test PostgreSQL server and `TEST_POSTGRES_ADMIN_URL` above:

```bash
.venv/bin/python -m pytest -p no:cacheprovider --run-integration \
  tests/test_vector_sync.py tests/integration/test_vector_sync.py -q
```

Qdrant coverage uses the real `AsyncQdrantClient(":memory:")` local engine, with
an isolated collection per test and client cleanup. No development Qdrant server,
model inference or downloads are used. This verifies local Qdrant semantics,
not the deployed server's HTTP transport.

The tests cover stable Steam app IDs, payloads and nullable metadata, vector
width, model/revision isolation, sparse cursor pagination, exact batch/total
limits, empty input, repeated upserts, changed embeddings, collection setup and
failure recovery. The sync reads PostgreSQL without committing or flushing caller
changes; the caller owns the session transaction and both resource lifetimes.
`max_games` must be positive when supplied and counts points visited, including
points already present in Qdrant. Successful runs return `processed`, `upserted`
and `batches`; upserts wait for completion.

`ensure_game_collection()` is now async and must be awaited. The sync assumes
that the configured collection already exists. It does not delete stale points,
change collections, or persist a cursor. After failure, rerun from the start;
completed batches are safely overwritten. Source changes behind a run's cursor
are picked up on the next run.

## Phase 6D similar-game retrieval

```bash
.venv/bin/python -m pytest -p no:cacheprovider tests/test_vector_store.py -q
# With the disposable PostgreSQL server and TEST_POSTGRES_ADMIN_URL above:
.venv/bin/python -m pytest -p no:cacheprovider --run-integration \
  tests/test_vector_store.py tests/integration/test_vector_retrieval.py -q
```

`await vector_store.find_similar_games(client, steam_app_id, top_k=5)` retrieves
an indexed target vector and uses `query_points` with point-ID self-exclusion.
Results contain `steam_app_id`, `name`, and `score`, in descending cosine-score
order. Equal-score ordering is unspecified. Non-positive limits and absent targets
return `[]`; collection/transport failures propagate. Missing names become empty
strings. A target without the expected unnamed dense vector raises `ValueError`.
The service neither creates collections nor closes the caller's client.

Local Qdrant tests cover ranking, limits, identical vectors, self-exclusion,
empty/missing targets, payload mapping, and failures. The PostgreSQL integration
test syncs a synthetic sample and compares results to the unchanged Phase 5
`game_similarity.find_similar_games` baseline. No model weights are downloaded.

Manual verification also used read-only calls against five existing indexed games.
For Left 4 Dead 2 (550), the nearest indexed games were Killing Floor (0.550205),
Counter-Strike 2 (0.528072), Dota 2 (0.454495), and LEGO Harry Potter: Years 1-4
(0.330766). The broader PostgreSQL baseline includes games not yet indexed in
Qdrant, so comparing its top-k directly requires accounting for candidate coverage.
This is a small qualitative check, not a retrieval-quality evaluation or benchmark.
For both Left 4 Dead 2 and Dota 2, restricting the Python cosine comparison to
those same five indexed games gave identical rankings and scores to six decimal
places. The manual calls exercised the running Qdrant server without writing data.

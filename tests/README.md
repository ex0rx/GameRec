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

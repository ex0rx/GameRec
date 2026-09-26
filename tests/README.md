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

## Phase 6E metadata filtering

```python
# Explicit setup for an existing/new collection (outside the search path):
await ensure_game_collection(client)
await ensure_game_payload_indexes(client)

results = await find_similar_games(
    client,
    steam_app_id=550,
    top_k=5,
    genres=["Action"],
    categories=["Co-op", "Multi-player"],
)
```

These functions are in `gamerec.services.vector_store`. Each supplied value adds
one required exact-match condition: the example requires Action AND Co-op AND
Multi-player. Matching is case-sensitive; no trimming or normalization is done.
`None` and empty lists leave that field unrestricted. Filters apply to candidates,
not to the target lookup. Missing/null/empty payload fields do not match requested
values. Self-exclusion, score ordering, limits, and unfiltered results are preserved.

`ensure_game_payload_indexes(client)` creates keyword indexes for only `genres`
and `categories`, waits for completion, and skips existing keyword indexes.
It checks both existing types before writing and raises `ValueError` for an
incompatible index, without replacing it. A partially completed setup can be
rerun. Search never creates indexes; callers explicitly perform setup beforehand.

```bash
.venv/bin/python -m pytest -p no:cacheprovider \
  tests/test_vector_store.py tests/test_vector_indexes.py tests/test_vector_sync.py -q
```

Filtering tests use in-memory Qdrant; index lifecycle tests mock the async client
because local Qdrant does not build payload indexes. Additional smoke verification
used a disposable Qdrant container with temporary storage: both keyword indexes
were visible in collection metadata, repeating setup issued no additional index
writes, four sample points were preserved, and AND/no-match queries passed.
No development collections were changed. Performance and large-catalogue retrieval
quality are outside this phase.

## Phase 6F consistency and collection versions

Collections created by `ensure_game_collection()` now store a
`gamerec_embeddings` binding in Qdrant collection metadata: model name, revision,
and vector size. Setup, search, sync, upsert, and deletion validate that binding
and the actual unnamed cosine-vector configuration. Mismatches raise `ValueError`
before point writes. This requires Qdrant server 1.16+ (collection metadata).

Legacy collections without a binding are rejected, including during search.
Their provenance cannot be inferred from vector size. Rebuild under a new name;
no legacy data is relabelled or migrated automatically. Using the configured
embedding model/revision, rebuild without changing the active search collection:

```python
from gamerec.services.vector_store import (
    ensure_game_collection,
    ensure_game_payload_indexes,
)
from gamerec.services.vector_sync import (
    sync_embeddings_to_qdrant,
    prune_embeddings_from_qdrant,
)

name = "game_embeddings_v2"
await ensure_game_collection(client, collection_name=name)
await ensure_game_payload_indexes(client, collection_name=name)
stats = await sync_embeddings_to_qdrant(db, client, collection_name=name)
```

Use a separate process/configuration to build a different model revision while
an old search process remains active. The builder settings must select that
revision's existing PostgreSQL embeddings. Cutover is an explicit later settings
change; no aliases or active-collection changes happen during rebuilds.

Sync retrieves only Qdrant payloads for each bounded PostgreSQL page. Missing
points are inserted, changed `input_hash` or payload metadata is updated, and
unchanged points are skipped. Point IDs remain Steam app IDs. Vectors are not
compared over the network: changing vector contents without changing the hash is
not detected. PostgreSQL remains read-only. Counts are `processed`, `inserted`,
`updated`, `skipped`, `upserted` (inserted + updated), `deleted`, and `batches`
(nonempty PostgreSQL pages, including unchanged pages).

Pruning is explicit and scoped to one compatible collection:

```python
preview = await prune_embeddings_from_qdrant(db, client, collection_name=name)
# When intended, apply the same comparison against the current PostgreSQL set:
result = await prune_embeddings_from_qdrant(
    db,
    client,
    collection_name=name,
    dry_run=False,
)
```

The default dry run returns `scanned`, `candidates`, `deleted=0`, and Qdrant-page
`batches`. Each bounded Qdrant page is checked against all PostgreSQL embeddings
for the active model/revision, not just IDs visited in an earlier sync. A row
existing only for another revision is absent from this collection's source set.
An empty source previews all points as deletion candidates; explicitly applying
that comparison removes all points from that selected collection.

Alternatively, `sync_embeddings_to_qdrant(..., prune_missing=True)` prunes after
a successful full source scan. Combining it with `max_games` is rejected.
Default and bounded syncs never delete points. No other collections are modified.

Use a dedicated session against committed source data and pause concurrent
embedding/sync writers while pruning. Pending ORM changes are rejected, but
already-flushed uncommitted writes cannot be reliably distinguished from reads.
There is no distributed transaction or lock between PostgreSQL and Qdrant.
Failures propagate and completed batches remain applied; rerunning safely skips
matching points and retries remaining work. A dry run is a preview, not a frozen
plan: applying it rechecks the current source. Long-running concurrent source
changes may require another sync.

```bash
# With TEST_POSTGRES_ADMIN_URL pointing at the disposable server above:
.venv/bin/python -m pytest -p no:cacheprovider --run-integration \
  tests/test_vector_versions.py tests/test_vector_sync.py \
  tests/test_vector_store.py tests/test_vector_indexes.py \
  tests/integration/test_vector_sync.py \
  tests/integration/test_vector_consistency.py \
  tests/integration/test_vector_retrieval.py -q
```

Coverage includes mismatch/legacy rejection, hash and metadata updates, skipped
writes, new points, preview/apply pruning, revision isolation, partial-run safety,
empty sources, failed-run recovery, and preservation of old versions during
rebuild. The Phase 6C repeat-run counters above are superseded by these incremental
counts. Tests use disposable PostgreSQL and in-memory Qdrant; no model downloads.
A separate disposable-server smoke check also verified persisted collection
metadata, repeat-safe setup, revision-mismatch rejection, new-version creation,
and deletion scoped to the new version while preserving the old collection.

## Phase 6G retrieval benchmark

Run against existing configured PostgreSQL embeddings and a running Qdrant server:

```bash
docker compose exec -T api python -m gamerec.scripts.benchmark_retrieval \
  --sizes 1000 10000 50000 --top-k 10 --warmups 2 --repeats 10 \
  --output /app/tests/phase6g-benchmark.json
```

`--queries` accepts public Steam **app** IDs. Defaults span shooters (550, 730,
440), puzzle (620, 400), strategy (570), RPG (292030, 1245620), sandbox (105600)
and simulation (413150). These are a fixed convenience sample, not a statistically
representative relevance evaluation. Missing embeddings for the configured model
and revision are reported and skipped; duplicate IDs run once. Queries may live
outside a smaller candidate prefix, keeping the query set constant across sizes.

Methodology:

- A single read-only PostgreSQL SELECT loads the largest requested prefix ordered
  by app ID, plus any query embeddings outside that prefix. Each size uses its
  deterministic prefix; no embeddings are generated or changed. Requested sizes
  are capped at available rows, and duplicate actual sizes run only once.
- Each run creates a fresh `gamerec_benchmark_<uuid>` collection with exactly those
  candidate vectors and cosine distance. Batched upserts wait for application;
  collection readiness is checked with a bounded timeout and exact point count.
  The collection is deleted in `finally`, including on retrieval failure. The
  configured game collection is never touched. Hard process termination or a
  server outage can still leave a temporary collection behind; manual cleanup must
  target only that UUID-named benchmark collection.
- This is a **retrieval-kernel benchmark**, not timing the two application service
  wrappers. The exact side reuses the unchanged Phase 5 `cosine_similarity`, scans
  all candidates, and performs the same descending full sort. Integration coverage
  checks parity with `game_similarity.find_similar_games`. PostgreSQL fetch and
  deserialization happen once outside timing, so this deliberately gives the
  brute-force side a resident snapshot. It does not measure the Phase 5 service's
  per-call SQL cost.
- Both sides start with the same resident query vector and exclude its app ID.
  Qdrant uses `query_points` with the same ID-exclusion filter as current retrieval,
  with server-default search parameters. Its timing includes request serialization,
  network/service overhead and response parsing; target lookup, collection
  validation, names/payloads, setup and teardown are excluded on both sides.
- Each query gets two untimed warmups and ten timed repeats per method by default.
  Method order alternates by repetition. `time.perf_counter()` measures seconds;
  output is milliseconds. Median and nearest-rank p95 are pooled across all
  query/repeat samples per method, not averages of per-query percentiles.
- Overlap is set intersection divided by effective k: `min(top_k, candidates
  after self-exclusion)`. Queries with no candidates are reported and excluded
  from summaries. Average overlap averages repeats, then queries. Exact ties keep
  app-ID order; Qdrant tie ordering and float32 normalization may differ.
- Optional JSON includes raw samples, per-query summaries, overlap per repeat,
  last-repeat rankings, shared-ID rank deltas (Qdrant rank minus exact rank),
  corpus/query hashes, versions and collection/index configuration. It stores no
  embedding vectors or connection credentials. Matching hashes identify the input
  snapshot; rerunning after embeddings change is a different experiment.

Actual server results are recorded in [phase6g-benchmark.json](phase6g-benchmark.json).
The run used the existing 384-dimensional `all-MiniLM-L6-v2` vectors, pinned
revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, Python 3.13.15, Qdrant server
and client 1.19.1, via the Compose API container on WSL2. See the artifact's UTC
capture time. Five available queries were 550, 570, 730, 292030 and 105600;
620, 400, 440, 413150 and 1245620 were missing. Each row contains 50 measured
calls per method, top-k 10, after two warmups per query/method.

| Actual corpus | Queries | Brute median / p95 (ms) | Qdrant median / p95 (ms) | Mean top-10 overlap |
| ---: | ---: | ---: | ---: | ---: |
| 1,000 | 5 | 22.849 / 23.556 | 2.261 / 3.052 | 100% |
| 7,144 (requested 10k) | 5 | 164.541 / 170.632 | 2.858 / 3.490 | 100% |

The 50k request reused the same available 7,144 rows and was skipped. No data was
synthesized to reach requested sizes. Both collections reported
`indexed_vectors_count=0`: this measures Qdrant's unindexed retrieval at these
sizes, **not HNSW recall or ANN scaling**. Qdrant may leave small segments unindexed
under its default thresholds; see the official
[collection/indexing documentation](https://qdrant.tech/documentation/manage-data/collections/).
Readiness and applied writes do not prove every vector has an HNSW index.

Tiny datasets may favour brute force because Qdrant has service overhead. Qdrant
should scale better, but these limited results do not establish a general crossover
point, 10k/50k performance, or relevance quality. A larger stored corpus, confirmed
HNSW indexing, more available queries, repeated independent runs and a controlled
machine/load are needed for those conclusions. No optimized NumPy baseline,
concurrency/load test, metadata filtering, model inference or production service
latency is included.

Focused verification (no downloads or wall-clock thresholds):

```bash
.venv/bin/python -m pytest -p no:cacheprovider tests/test_benchmark_retrieval.py tests/test_similarity.py -q
# With the isolated PostgreSQL fixture setup described above:
.venv/bin/python -m pytest -p no:cacheprovider --run-integration tests/integration/test_benchmark_retrieval.py -q
```

Validation for this change:

- Focused unit command above: 27 passed (19 benchmark checks and 8 cosine checks).
- `TEST_POSTGRES_ADMIN_URL=<isolated-test-url> .venv/bin/python -m pytest -p
  no:cacheprovider --run-integration tests -q`: 233 passed, 28 failed, two warnings.
  All failures are existing vector retrieval tests (27 unit, one integration):
  the current service returns `(results, target_name)` while those tests expect
  a list, and target retrieval now requests payloads. The new snapshot integration
  test passes. These unrelated service/test mismatches were left unchanged.
- `.venv/bin/ruff check --no-cache .`: two existing I001 import-order errors in
  `create_qdrant_collection.py` and `get_steam_catalogue.py`. Ruff check and format
  check pass for all three new Python files. No type checker is configured.
- An initial sandboxed default-suite run stalled and was interrupted; the complete
  suite above ran successfully to completion outside that restriction against a
  disposable PostgreSQL container, which was then removed. No live model download
  or development database writes were needed.

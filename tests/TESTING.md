# GameRec Testing Guide

This guide is the standard way to run GameRec tests without polluting development data or leaving temporary containers behind.

## Principles

1. **Unit tests should not depend on PostgreSQL, Qdrant, Steam, or model downloads.**
2. **Integration tests must use isolated test data.**
3. **Do not run destructive tests against the development database or development Qdrant collection.**
4. Use `docker compose run --rm ...` for temporary test containers so they are removed automatically.
5. Do **not** use `docker compose down -v` unless you intentionally want to delete development PostgreSQL/Qdrant volumes.

## Test types

### Unit tests

Examples:
- embedding-text preparation
- cosine similarity
- payload/filter construction
- benchmark/statistics calculations
- pure transformation functions
- fake-model embedding tests

These should be fast and should not require real external services.

### Integration tests

Examples:
- SQLAlchemy queries/upserts
- embedding persistence
- PostgreSQL -> Qdrant sync
- Qdrant search/filtering
- pruning/reconciliation
- API endpoint behaviour

These exercise real components and therefore require an isolated test database and/or test Qdrant collection.

## 1. Normal API test suite

The commands in sections 1–4 skip PostgreSQL integration tests unless explicitly
enabled. For the complete embeddings-container workflow, including the required
test database and URL, follow [section 6](#6-postgresql-integration-tests-in-the-embeddings-container).

The API container intentionally does **not** contain `sentence-transformers`.

Run the normal suite with:

```bash
docker compose exec api uv run pytest
```

One directory:

```bash
docker compose exec api uv run pytest tests/integration -v
```

One file:

```bash
docker compose exec api uv run pytest tests/test_similarity.py -v
```

One test:

```bash
docker compose exec api uv run pytest   tests/test_similarity.py::test_identical_vectors -v
```

If collection fails with:

```text
ModuleNotFoundError: No module named 'sentence_transformers'
```

the test imports embedding-worker-only code and should either:
- run in the embedding test environment, or
- be refactored so the test only imports lightweight interfaces/fakes.

Do not install `sentence-transformers` into the production API image just to make tests pass.

## 2. Tests requiring SentenceTransformers

The dedicated `embeddings` service owns the ML dependencies.

Use a temporary container:

```bash
docker compose run --rm   -v "$PWD/tests:/app/tests:ro"   embeddings   uv run --group dev --group embeddings   python -m pytest tests/ -v
```

Run only the relevant embedding tests when possible:

```bash
docker compose run --rm   -v "$PWD/tests:/app/tests:ro"   embeddings   uv run --group dev --group embeddings   python -m pytest tests/integration/test_game_embeddings.py -v
```

`--rm` removes the temporary container after pytest exits.

If the embedding image contains an older copy of `src/`, rebuild it first:

```bash
docker compose build embeddings
```

or:

```bash
docker compose run --rm --build   -v "$PWD/tests:/app/tests:ro"   embeddings   uv run --group dev --group embeddings   python -m pytest tests/ -v
```

## 3. Integration-test isolation

Before running integration tests, verify that fixtures are **not pointing at development data**.

Never allow tests to modify:
- the normal GameRec development PostgreSQL database
- the active development Qdrant collection such as `game_embeddings_v1` / `game_embeddings_v2`

Use dedicated test resources, for example:

```text
PostgreSQL:
gamerec_test

Qdrant:
test_game_embeddings
```

The exact names should come from the repository's test configuration/fixtures.

A good integration fixture should:
1. create or prepare isolated test state
2. run the test
3. rollback/truncate/drop temporary state
4. delete temporary Qdrant collections/points
5. close sessions and clients

Tests should leave the development dataset unchanged.

## 4. Recommended development workflow

While implementing a function, run only its relevant tests.

Example:

```bash
docker compose exec api uv run pytest tests/test_similarity.py -v
```

After finishing a feature, run its integration tests.

```bash
docker compose exec api uv run pytest tests/integration -v
```

If those tests require ML dependencies, run them through the temporary `embeddings` container instead.

Before committing, run the complete applicable suite plus quality checks:

```bash
docker compose exec api uv run pytest
docker compose exec api uv run ruff check .
```

If configured:

```bash
docker compose exec api uv run mypy src
```

Then run any embedding-only tests separately.

## 5. Qdrant integration tests

Qdrant tests should use a dedicated test collection.

Typical lifecycle:

```text
create test collection
        |
insert test points
        |
run search/filter/sync assertions
        |
delete test collection
```

Do not reuse the active development collection.

For reconciliation/pruning tests, create known test points explicitly so expected stale/missing points are deterministic.

## 6. PostgreSQL integration tests in the embeddings container

Run these commands from the repository root in **WSL Bash**, not PowerShell or
Python. The complete suite needs both `TEST_POSTGRES_ADMIN_URL` inside the test
container and pytest's `--run-integration` flag. Without the flag, PostgreSQL
integration tests are skipped, even when you select `tests/integration`.

The current `tests/integration/conftest.py` fixture requires an existing database
named **`gamerec_test_admin`** on a dedicated test server. For each test it creates
a UUID-named database, creates tables from SQLAlchemy metadata, and drops that
database afterward. The test role needs permission to create databases. No manual
Alembic migration or development database changes are needed.

### 1. Create the temporary Compose override

Paste this entire block into the terminal:

```bash
cat > /tmp/gamerec-tests.yml <<'YAML'
services:
  test-db:
    image: postgres:17
    environment:
      POSTGRES_USER: gamerec_test
      POSTGRES_PASSWORD: synthetic-test-password
      POSTGRES_DB: gamerec_test_admin
    tmpfs:
      - /var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U gamerec_test -d gamerec_test_admin"]
      interval: 2s
      timeout: 3s
      retries: 30
YAML
```

These credentials are synthetic and only for this disposable test server.
The command creates a file; no output on success is normal. Verify it with:

```bash
cat /tmp/gamerec-tests.yml
```

If Bash shows a `>` prompt, it is waiting for the closing `YAML`. Enter `YAML`
on its own line with no surrounding spaces, then press Enter. Or press Ctrl+C
and paste the block again. Do not include formatting artifacts such as `&#x20;`,
`\_`, or `\-` in the pasted command.

### 2. Start the test database and wait for readiness

```bash
docker compose -f compose.yml -f /tmp/gamerec-tests.yml \
  up -d --wait test-db
```

This service uses temporary memory-backed storage and no development volumes.

### 3. Run unit and integration tests together

```bash
docker compose -f compose.yml -f /tmp/gamerec-tests.yml \
  run --rm --build --no-deps \
  -v "$PWD/tests:/app/tests:ro" \
  -e TEST_POSTGRES_ADMIN_URL='postgresql+asyncpg://gamerec_test:synthetic-test-password@test-db:5432/gamerec_test_admin' \
  embeddings \
  uv run --locked --group dev --group embeddings \
  python -m pytest -p no:cacheprovider --run-integration tests -v
```

- `test-db:5432` is reachable on the shared Compose network. `localhost` inside
  the embeddings container would refer to the embeddings container itself.
- `-e` explicitly passes the test URL into the container; setting a host variable
  alone does not pass it through.
- `--group dev --group embeddings` supplies pytest and the ML dependencies. The
  embeddings image normally excludes dev dependencies.
- `--build` refreshes the image's copy of `src/`. The read-only mount supplies
  `tests/`, which the embeddings Dockerfile does not copy.
- `--no-deps` avoids starting the development database unnecessarily.
- `--rm` removes the temporary embeddings container after pytest exits.
- `-p no:cacheprovider` avoids writing pytest's cache during the run.

To run **only integration tests**, replace `tests` in the last line with
`tests/integration`. For just embedding persistence tests, replace it with
`tests/integration/test_game_embeddings.py`. Keep `--run-integration` in both cases.

The tests use fake embedding models and block model downloads. Installing missing
Python dependencies through `uv` may still require network access. The current
Qdrant tests use isolated in-memory clients; no development Qdrant server is needed.

### 4. Clean up after testing

Run these commands after pytest finishes, including when tests fail:

```bash
docker compose -f compose.yml -f /tmp/gamerec-tests.yml \
  rm -s -f test-db

rm /tmp/gamerec-tests.yml
```

This removes the disposable PostgreSQL container and its temporary storage. The
embeddings container removes itself through `--rm`. Development PostgreSQL and
Qdrant volumes remain intact. Do not use `docker compose down -v` for test cleanup.

## 7. External services

Automated tests should not depend on:
- live Steam API calls
- downloading Hugging Face models
- development Steam catalogue data

Use:
- deterministic fake embedding models
- mocked HTTP responses
- small explicit database fixtures
- isolated Qdrant points

This keeps tests fast, reproducible, and safe.

## 8. Cleanup after testing

### Temporary containers

Containers started with:

```bash
docker compose run --rm ...
```

are automatically deleted.

Check for leftovers:

```bash
docker compose ps -a
```

### Stop normal development services

```bash
docker compose stop
```

This stops containers but preserves PostgreSQL and Qdrant data.

### Remove containers/network while preserving volumes

```bash
docker compose down
```

Named development volumes remain.

### Do NOT normally run

```bash
docker compose down -v
```

`-v` deletes named volumes and can erase the development PostgreSQL database and Qdrant index.

Use it only when intentionally resetting all local persisted data.

## 9. Quick pre-commit checklist

```text
[ ] Relevant unit tests pass
[ ] Relevant integration tests pass
[ ] Tests use isolated PostgreSQL/Qdrant state
[ ] No live Steam/model dependency in automated tests
[ ] Ruff passes
[ ] mypy passes if configured
[ ] Temporary test containers removed
[ ] Development database unchanged
[ ] Development Qdrant collection unchanged
```

Useful commands:

```bash
docker compose exec api uv run pytest
docker compose exec api uv run ruff check .
docker compose ps -a
git status
```

For embedding-only tests:

```bash
docker compose run --rm   -v "$PWD/tests:/app/tests:ro"   embeddings   uv run --group dev --group embeddings   python -m pytest tests/ -v
```

## 10. Mental model

```text
API/lightweight tests
        |
existing api container

ML dependency tests
        |
temporary embeddings container --rm

DB/Qdrant integration tests
        |
isolated test resources
        |
fixture cleanup

Development PostgreSQL + Qdrant
        |
never touched by destructive tests
```

The key rule is: **test isolation matters more than whether the test runs in a new or existing Docker container.**

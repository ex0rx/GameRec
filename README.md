# Phase 5C embedding worker

From the repository root, with the existing Compose `.env` configured:

```bash
docker compose build embeddings
docker compose run --rm embeddings
```

The worker uses the same `DATABASE_URL` and settings as the API. The database
hostname in that URL must be `db`, the existing Compose service name. Keep
credentials in the local `.env`. Compose waits for the database healthcheck.

`Dockerfile.embeddings` installs the project and its `embeddings` dependency
group with `uv sync --locked --no-dev --group embeddings`. It uses Python 3.13
to match the project requirement. The API Dockerfile and its dependencies are
unchanged. The worker copies source into its image; rebuild after code changes.
The existing lockfile includes PyTorch CUDA dependencies on Linux, so the first
build can be large even though this job does not require a GPU.

The `embeddings` profile keeps the worker out of ordinary `docker compose up`.
Explicitly targeting it with `run` activates it automatically. It publishes no
ports and exits when the script finishes.

The script calls the existing `generate_game_embeddings()` service using
`SessionLocal` for Steam app IDs `105600` and `730`. The service reads game
metadata, builds text and uses `sentence-transformers/all-MiniLM-L6-v2`.
The script prints each returned vector's dimensions and fails if they differ
from 384. Missing results are reported as a missing game or unusable text:
the service's return value does not distinguish these cases. No results are
persisted and no metadata is fetched from Steam.

The first run downloads model files into the named `embedding_model_cache`
volume at `HF_HOME=/opt/model-cache`. This volume survives `run --rm`.
After a successful first run, verify cache reuse without Hub access:

```bash
docker compose run --rm -e HF_HUB_OFFLINE=1 embeddings
```

Python dependencies are locked; the existing service selects the model by name
without pinning a model revision. This milestone preserves that behavior.

## Verification (24 September 2026)

- Image build and both normal and offline runs completed with exit code 0.
- Both requested games had description, genre and category metadata in the
  existing database and returned 384-dimensional vectors.
- The configured `db` hostname resolved; the worker queried it successfully.
- API health and database-backed catalogue requests returned HTTP 200 after
  execution. SentenceTransformers remained absent from the API container.
- Existing tests in the API container: 97 passed, 36 integration tests skipped.
  The 14 game-text tests also passed locally. A full local test run stalled and
  was interrupted; the complete container run above succeeded.
- Entry-point smoke checks with a mocked service covered complete, partial,
  empty and wrong-dimension results, plus session and engine cleanup.
- Compose validation and entry-point Ruff checks passed. Repository-wide Ruff
  still reports three pre-existing issues: import formatting in `game_text.py`
  and `game_embeddings.py`, and an unused `util` import in the latter.

Reference: [Compose profiles](https://docs.docker.com/compose/how-tos/profiles/),
[uv locked syncing](https://docs.astral.sh/uv/concepts/projects/sync/), and
[Hugging Face cache/offline settings](https://huggingface.co/docs/huggingface_hub/main/package_reference/environment_variables).

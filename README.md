# GameRec

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
returned. Games without description, genres or categories receive the existing
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
below; stopping it makes explanation requests with descriptive evidence fail
with 503 while ordinary search remains available when its own dependencies
are running. Explanations are generated per request, without caching or database
writes. Partial matches are allowed, and reasonable inference and established
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

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

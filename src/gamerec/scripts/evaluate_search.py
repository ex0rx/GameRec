"""Export fixed search queries and benchmark warm FastAPI request latency."""

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from gamerec.schemas.search import SearchGamesResponse
from gamerec.services.recommendation_evaluation import (
    latency_summary,
    relevance_metrics,
)

SEARCH_QUERIES = (
    (
        "open_world_rpg",
        "Open-world RPG with deep character progression and a strong story",
    ),
    ("fantasy_rpg", "Fantasy RPG with quests, exploration, and meaningful choices"),
    ("survival_crafting", "Cooperative survival crafting game with challenging bosses"),
    ("fast_fps", "Fast-paced first-person shooter with intense combat"),
    ("tactical_fps", "Tactical first-person shooter with team-based multiplayer"),
    ("turn_based_strategy", "Turn-based strategy game with tactical battles"),
    ("story_adventure", "Story-driven adventure with memorable characters"),
    ("coop_multiplayer", "Cooperative multiplayer game for a group of friends"),
    ("roguelike", "Roguelike with varied runs and permanent upgrades"),
    ("relaxing_simulation", "Relaxing farming or life simulation game"),
    ("difficult_action_rpg", "Difficult action RPG with demanding boss fights"),
    ("exploration", "Atmospheric exploration game with hidden places to discover"),
)
BENCHMARK_QUERY_IDS = ("open_world_rpg", "survival_crafting", "fast_fps")
TOP_K = 10


def load_labels(path: Path | None) -> dict[str, dict[int, int]]:
    """Read optional grades as {query_id: {steam_app_id: 0|1|2}}."""
    if path is None:
        return {}

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        values: dict[str, object] = {}
        for key, value in pairs:
            if key in values:
                raise ValueError(f"Duplicate label key: {key}")
            values[key] = value
        return values

    raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if not isinstance(raw, dict):
        raise TypeError("Labels must be an object keyed by query ID")
    known_ids = {query_id for query_id, _ in SEARCH_QUERIES}
    labels: dict[str, dict[int, int]] = {}
    for query_id, grades in raw.items():
        if query_id not in known_ids or not isinstance(grades, dict):
            raise ValueError(f"Unknown query ID or invalid labels: {query_id}")
        labels[query_id] = {}
        for app_id, grade in grades.items():
            if (
                not isinstance(app_id, str)
                or not app_id.isdecimal()
                or str(int(app_id)) != app_id
                or int(app_id) <= 0
                or type(grade) is not int
                or grade not in (0, 1, 2)
            ):
                raise ValueError(f"Invalid app ID or grade for {query_id}: {app_id}")
            labels[query_id][int(app_id)] = grade
    return labels


async def request_search(client: httpx.AsyncClient, query: str) -> SearchGamesResponse:
    response = await client.get(
        "/search/games", params={"query": query, "limit": TOP_K}
    )
    response.raise_for_status()
    return SearchGamesResponse.model_validate(response.json())


def evaluate_result(
    query_id: str, response: SearchGamesResponse, labels: dict[int, int]
) -> dict:
    ids = [game.steam_app_id for game in response.games]
    judged_top_10 = {app_id: labels[app_id] for app_id in ids if app_id in labels}
    existing = relevance_metrics(ids, judged_top_10, TOP_K)
    sufficient = len(ids) == TOP_K and existing["complete"]
    return {
        "query_id": query_id,
        "query": response.query,
        "returned_count": len(ids),
        "labelled_count": existing["labelled_count"],
        "labelled_coverage": existing["labelled_coverage"],
        "missing_label_ids": existing["missing_label_ids"],
        "unfilled_top_10_slots": TOP_K - len(ids),
        "sufficient_labels": sufficient,
        "precision_at_10": existing["relevant_at_k"] / TOP_K if sufficient else None,
        "ndcg_at_10": existing["ndcg_at_k"] if sufficient else None,
        "average_graded_relevance_at_10": (
            existing["average_relevance_at_k"] if sufficient else None
        ),
        "games": [
            {**game.model_dump(), "relevance_grade": labels.get(game.steam_app_id)}
            for game in response.games
        ],
    }


async def benchmark_query(
    client: httpx.AsyncClient, query: str, *, warmups: int, runs: int
) -> dict:
    for _ in range(warmups):
        await request_search(client, query)

    samples_ms: list[float] = []
    error_count = 0
    for _ in range(runs):
        start = time.perf_counter()
        try:
            await request_search(client, query)
        except (httpx.HTTPError, ValueError):
            error_count += 1
        else:
            samples_ms.append((time.perf_counter() - start) * 1000)
    return {
        "warmups": warmups,
        "measured_requests": runs,
        "error_count": error_count,
        "successful_latency_ms": latency_summary(samples_ms),
    }


async def evaluate(
    client: httpx.AsyncClient,
    labels: dict[str, dict[int, int]],
    *,
    warmups: int,
    runs: int,
) -> dict:
    queries = []
    for query_id, query in SEARCH_QUERIES:
        queries.append(
            evaluate_result(
                query_id, await request_search(client, query), labels.get(query_id, {})
            )
        )

    benchmark_queries = dict(SEARCH_QUERIES)
    performance = []
    for query_id in BENCHMARK_QUERY_IDS:
        performance.append(
            {
                "query_id": query_id,
                **await benchmark_query(
                    client, benchmark_queries[query_id], warmups=warmups, runs=runs
                ),
            }
        )
    return {
        "evaluated_at_utc": datetime.now(UTC).isoformat(),
        "precision_relevance_threshold": 1,
        "metric_rule": "All 10 returned games must have manual grades 0, 1, or 2",
        "queries": queries,
        "performance": performance,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--labels", type=Path)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("benchmark_results/search")
    )
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--runs", type=int, default=20)
    args = parser.parse_args(argv)
    if args.warmups not in (2, 3) or args.runs != 20:
        parser.error("warmups must be 2 or 3; measured runs must be 20")
    return args


async def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    labels = load_labels(args.labels)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=60.0) as client:
        report = await evaluate(client, labels, warmups=args.warmups, runs=args.runs)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = args.output_dir / f"evaluation_{timestamp}.json"
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    print(f"Wrote {output}")


if __name__ == "__main__":
    asyncio.run(main())

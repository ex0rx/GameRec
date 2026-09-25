import argparse
import asyncio
import hashlib
import json
import math
import platform
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from statistics import mean, median
from time import perf_counter
from uuid import uuid4

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    Filter,
    HasIdCondition,
    PointStruct,
    VectorParams,
)
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.core.config import settings
from gamerec.db import SessionLocal, engine
from gamerec.integrations.qdrant import get_qdrant_client
from gamerec.ml.similarity import cosine_similarity
from gamerec.models.game_embedding import GameEmbedding

# Public app IDs: shooters, puzzle, strategy, RPG, sandbox and simulation.
DEFAULT_QUERIES = [550, 620, 570, 730, 400, 440, 292030, 413150, 105600, 1245620]
Corpus = dict[int, list[float]]


def latency_stats(samples: list[float]) -> dict[str, float]:
    """Seconds to milliseconds; p95 uses the nearest-rank convention."""
    if not samples:
        raise ValueError("At least one timing sample is required")
    ordered = sorted(samples)
    return {
        "median_ms": median(ordered) * 1000,
        "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1] * 1000,
    }


def top_k_overlap(exact: list[int], approximate: list[int], k: int) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    return len(set(exact[:k]) & set(approximate[:k])) / k


def select_queries(queries: list[int], vectors: Corpus) -> tuple[list[int], list[int]]:
    unique = list(dict.fromkeys(queries))
    return (
        [q for q in unique if q in vectors],
        [q for q in unique if q not in vectors],
    )


def exact_search(
    corpus: Corpus, query_id: int, vector: list[float], k: int
) -> list[int]:
    # Preserve Phase 5's cosine function, full scan and descending full sort.
    scores = [
        (appid, cosine_similarity(vector, candidate))
        for appid, candidate in corpus.items()
        if appid != query_id
    ]
    scores.sort(key=lambda item: item[1], reverse=True)
    return [appid for appid, _ in scores[:k]]


async def load_snapshot(db: AsyncSession, limit: int, queries: list[int]) -> Corpus:
    eligible = select(GameEmbedding.steam_app_id, GameEmbedding.embedding).where(
        GameEmbedding.model_name == settings.embeddings_model_name,
        GameEmbedding.model_revision == settings.embeddings_model_revision,
    )
    # One SELECT gives a consistent snapshot including queries outside the prefix.
    prefix = (
        eligible.with_only_columns(GameEmbedding.steam_app_id)
        .order_by(GameEmbedding.steam_app_id)
        .limit(limit)
    )
    rows = await db.execute(
        eligible.where(
            or_(
                GameEmbedding.steam_app_id.in_(prefix),
                GameEmbedding.steam_app_id.in_(queries),
            )
        ).order_by(GameEmbedding.steam_app_id)
    )
    return {appid: list(vector) for appid, vector in rows}


async def benchmark_corpus(
    client: AsyncQdrantClient,
    corpus: Corpus,
    queries: Corpus,
    *,
    top_k: int,
    warmups: int,
    repeats: int,
) -> dict:
    if not corpus or not queries or top_k < 1 or warmups < 1 or repeats < 1:
        raise ValueError(
            "Nonempty corpus/queries and positive k/warmups/repeats required"
        )
    dimensions = len(next(iter(corpus.values())))
    for vector in [*corpus.values(), *queries.values()]:
        if len(vector) != dimensions or not all(math.isfinite(x) for x in vector):
            raise ValueError("Vectors must have matching dimensions and finite values")
        cosine_similarity(vector, vector)  # Reject empty/zero vectors before setup.
    name = f"gamerec_benchmark_{uuid4().hex}"
    created = False
    try:
        await client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dimensions, distance=Distance.COSINE),
        )
        created = True
        items = list(corpus.items())
        for start in range(0, len(items), 256):
            await client.upsert(
                collection_name=name,
                points=[
                    PointStruct(id=i, vector=v) for i, v in items[start : start + 256]
                ],
                wait=True,
            )
        deadline = perf_counter() + 120
        while True:
            info = await client.get_collection(name)
            if info.status == "green" and info.optimizer_status == "ok":
                break
            if perf_counter() >= deadline:
                raise TimeoutError("Benchmark collection did not become ready")
            await asyncio.sleep(0.5)
        if (await client.count(name, exact=True)).count != len(corpus):
            raise ValueError("Qdrant corpus count differs from snapshot")

        async def qdrant_search(qid: int, vector: list[float], k: int) -> list[int]:
            response = await client.query_points(
                collection_name=name,
                query=vector,
                query_filter=Filter(must_not=[HasIdCondition(has_id=[qid])]),
                limit=k,
                with_payload=False,
                with_vectors=False,
            )
            return [int(point.id) for point in response.points]

        timings: dict[str, list[float]] = {"brute_force": [], "qdrant": []}
        details = []
        for qid, vector in queries.items():
            k = min(top_k, len(corpus) - int(qid in corpus))
            if k == 0:
                continue
            local: dict[str, list[float]] = {"brute_force": [], "qdrant": []}
            overlaps = []
            results: dict[str, list[int]] = {}
            for run in range(warmups + repeats):
                methods = ["brute_force", "qdrant"]
                if run % 2:
                    methods.reverse()
                for method in methods:
                    started = perf_counter()
                    result = (
                        exact_search(corpus, qid, vector, k)
                        if method == "brute_force"
                        else await qdrant_search(qid, vector, k)
                    )
                    elapsed = perf_counter() - started
                    results[method] = result
                    if run >= warmups:
                        local[method].append(elapsed)
                if run >= warmups:
                    overlaps.append(
                        top_k_overlap(results["brute_force"], results["qdrant"], k)
                    )
            for method, samples in timings.items():
                samples.extend(local[method])
            exact, approximate = results["brute_force"], results["qdrant"]
            details.append(
                {
                    "steam_app_id": qid,
                    "effective_k": k,
                    "overlap": mean(overlaps),
                    "overlap_per_repeat": overlaps,
                    "rank_deltas": {
                        str(i): approximate.index(i) - exact.index(i)
                        for i in exact
                        if i in approximate
                    },
                    "results_last_repeat": results,
                    "latency": {m: latency_stats(t) for m, t in local.items()},
                    "samples_seconds": local,
                }
            )
        return {
            "corpus_size": len(corpus),
            "query_count": len(details),
            "corpus_sha256": hashlib.sha256(json.dumps(items).encode()).hexdigest(),
            "query_sha256": hashlib.sha256(
                json.dumps(list(queries.items())).encode()
            ).hexdigest(),
            "latency": {m: latency_stats(t) for m, t in timings.items() if t},
            "average_overlap": mean(d["overlap"] for d in details) if details else None,
            "queries_without_candidates": [
                q for q in queries if len(corpus) == int(q in corpus)
            ],
            "qdrant_collection": info.model_dump(mode="json"),
            "queries": details,
        }
    finally:
        if created:
            await client.delete_collection(name)


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes", nargs="+", type=positive, default=[1000, 10000, 50000]
    )
    parser.add_argument("--queries", nargs="+", type=positive, default=DEFAULT_QUERIES)
    parser.add_argument("--top-k", type=positive, default=10)
    parser.add_argument("--warmups", type=positive, default=2)
    parser.add_argument("--repeats", type=positive, default=10)
    parser.add_argument(
        "--output", type=Path, help="Optional JSON including per-query details"
    )
    args = parser.parse_args()
    client = get_qdrant_client()
    try:
        async with SessionLocal() as db:
            snapshot = await load_snapshot(db, max(args.sizes), args.queries)
        found, missing = select_queries(args.queries, snapshot)
        print(f"Missing query app IDs: {missing}")
        if not found or not snapshot:
            print("No benchmarkable queries/embeddings for configured model/revision.")
            return
        report = {
            "timestamp": datetime.now(UTC).isoformat(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "qdrant_client": version("qdrant-client"),
            "qdrant_server": (await client.info()).version,
            "model": settings.embeddings_model_name,
            "revision": settings.embeddings_model_revision,
            "top_k": args.top_k,
            "warmups": args.warmups,
            "repeats": args.repeats,
            "requested_sizes": args.sizes,
            "timing_scope": "resident cosine scan/sort vs Qdrant query round trip; setup and target lookup excluded",
            "requested_queries": args.queries,
            "missing_queries": missing,
            "runs": [],
        }
        print("corpus queries brute median/p95 ms  qdrant median/p95 ms overlap")
        seen = set()
        for size in sorted(set(args.sizes)):
            corpus = dict(list(snapshot.items())[:size])
            if len(corpus) in seen:
                print(f"Requested {size}: same available corpus; duplicate run skipped")
                continue
            seen.add(len(corpus))
            result = await benchmark_corpus(
                client,
                corpus,
                {q: snapshot[q] for q in found},
                top_k=args.top_k,
                warmups=args.warmups,
                repeats=args.repeats,
            )
            result["requested_size"] = size
            report["runs"].append(result)
            if not result["query_count"]:
                print(f"{len(corpus)}: no candidates after self-exclusion")
                continue
            b, q = result["latency"]["brute_force"], result["latency"]["qdrant"]
            print(
                f"{len(corpus):6} {result['query_count']:7} "
                f"{b['median_ms']:.3f}/{b['p95_ms']:.3f} "
                f"{q['median_ms']:.3f}/{q['p95_ms']:.3f} "
                f"{result['average_overlap']:.3f}"
            )
        if args.output:
            args.output.write_text(json.dumps(report, indent=2) + "\n")
    finally:
        await client.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

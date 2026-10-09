"""Compare raw and hybrid search for a few known games; read-only."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter

from gamerec.core.config import settings
from gamerec.db import SessionLocal, engine
from gamerec.integrations.qdrant import get_qdrant_client
from gamerec.ml.embedding_model import embed_search_query, load_embedding_model
from gamerec.services.hybrid_search import HybridSearchResult, hybrid_search_games

EXAMPLES = (
    (
        "Open-world RPG with deep character progression and a strong story",
        (
            ("Cyberpunk 2077", 1091500),
            ("The Witcher 3", 292030),
            ("Skyrim Special Edition", 489830),
        ),
    ),
    (
        "Cooperative survival crafting game with challenging bosses",
        (("Valheim", 892970), ("V Rising", 1604030), ("Grounded", 962130)),
    ),
    (
        "Fast-paced first-person shooter with intense combat",
        (("DOOM", 379720), ("ULTRAKILL", 1229490), ("Titanfall 2", 1237970)),
    ),
)


@dataclass(frozen=True)
class RecallDiagnostic:
    name: str
    steam_app_id: int
    raw_rank: int | None
    similarity_score: float | None
    hybrid_rank: int | None
    indexed: bool
    status: str


def diagnose_expected_games(
    result: HybridSearchResult,
    expected: Sequence[tuple[str, int]],
    indexed_ids: set[int],
) -> list[RecallDiagnostic]:
    raw = {
        game["steam_app_id"]: (rank, game["score"])
        for rank, game in enumerate(result.raw_candidates, 1)
    }
    hybrid = {game.steam_app_id: rank for rank, game in enumerate(result.ranked, 1)}
    diagnostics = []
    for name, app_id in expected:
        raw_match = raw.get(app_id)
        if raw_match is None:
            status = "not in top 1000" if app_id in indexed_ids else "not indexed"
        elif app_id in result.missing_metadata_ids:
            status = "missing PostgreSQL metadata"
        elif app_id not in result.eligible_ids:
            status = "filtered by minimum reviews"
        elif app_id not in hybrid:
            status = "eligible, beyond returned top K"
        else:
            status = "ranked"
        diagnostics.append(
            RecallDiagnostic(
                name=name,
                steam_app_id=app_id,
                raw_rank=raw_match[0] if raw_match else None,
                similarity_score=raw_match[1] if raw_match else None,
                hybrid_rank=hybrid.get(app_id),
                indexed=app_id in indexed_ids,
                status=status,
            )
        )
    return diagnostics


async def main() -> None:
    model = load_embedding_model()
    client = get_qdrant_client()
    try:
        async with SessionLocal() as db:
            for query, expected in EXAMPLES:
                started = perf_counter()
                embedding = embed_search_query(query, model)
                result = await hybrid_search_games(
                    db, client, embedding, candidate_k=1000, top_k=1000
                )
                elapsed_ms = (perf_counter() - started) * 1000
                indexed_points = await client.retrieve(
                    collection_name=settings.qdrant_game_collection,
                    ids=[app_id for _, app_id in expected],
                    with_vectors=False,
                    with_payload=False,
                )
                indexed_ids = {int(point.id) for point in indexed_points}
                print(f"\nQuery: {query}")
                print(
                    f"Candidates: {len(result.raw_candidates)} | "
                    f"Eligible: {result.eligible_count} | Latency: {elapsed_ms:.1f} ms"
                )
                print("Expected games:")
                for item in diagnose_expected_games(result, expected, indexed_ids):
                    if item.raw_rank is None:
                        print(
                            f"  {item.name} ({item.steam_app_id}): {item.status}; "
                            f"indexed: {'yes' if item.indexed else 'no'}"
                        )
                        continue
                    similarity = f"{item.similarity_score:.4f}"
                    if item.hybrid_rank is None:
                        change = item.status
                    else:
                        delta = item.raw_rank - item.hybrid_rank
                        change = f"hybrid #{item.hybrid_rank} (change {delta:+d})"
                    print(
                        f"  {item.name} ({item.steam_app_id}): "
                        f"Qdrant #{item.raw_rank}, similarity {similarity}; {change}"
                    )
                print("Raw top 20:")
                for rank, game in enumerate(result.raw_candidates[:20], 1):
                    print(
                        f"  {rank:2d}. {game['name']} ({game['steam_app_id']}): "
                        f"similarity {game['score']:.4f}"
                    )
                print("Hybrid top 20:")
                for rank, game in enumerate(result.ranked[:20], 1):
                    print(
                        f"  {rank:2d}. {game.name} ({game.steam_app_id}): "
                        f"hybrid {game.hybrid_score:.4f}, "
                        f"similarity {game.similarity_score:.4f}, "
                        f"popularity {game.popularity_score:.4f}, "
                        f"Wilson {game.review_quality:.4f}"
                    )
    finally:
        try:
            await client.close()
        finally:
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

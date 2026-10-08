"""Read-only comparison of explicit filters on one natural-language query."""

import asyncio

from gamerec.db import SessionLocal, engine
from gamerec.integrations.qdrant import get_qdrant_client
from gamerec.ml.embedding_model import embed_search_query, load_embedding_model
from gamerec.services.hybrid_search import hybrid_search_games
from gamerec.services.search_filters import SearchFilters

QUERY = "Survival crafting with challenging bosses"
SCENARIOS = (
    ("No filters", None),
    ("Genre", SearchFilters(genres=["Action"])),
    ("Category", SearchFilters(categories=["Co-op"])),
    ("Release year", SearchFilters(release_year_from=2020)),
    (
        "Combined",
        SearchFilters(
            genres=["Action"],
            categories=["Co-op"],
            release_year_from=2020,
            min_reviews=500,
        ),
    ),
)


async def main() -> None:
    model = load_embedding_model()
    embedding = embed_search_query(QUERY, model)
    client = get_qdrant_client()
    try:
        async with SessionLocal() as db:
            for label, filters in SCENARIOS:
                result = await hybrid_search_games(
                    db, client, embedding, top_k=10, filters=filters
                )
                print(f"\nQuery: {QUERY}")
                print(f"Scenario: {label} | Filters: {filters}")
                print(
                    f"Candidates: retrieved={result.retrieved_count}, "
                    f"metadata={result.metadata_count}, "
                    f"eligible={result.eligible_count}, "
                    f"structured={result.structured_count}, "
                    f"returned={result.returned_count}"
                )
                for rank, game in enumerate(result.ranked, 1):
                    print(
                        f"  {rank:2d}. {game.name} ({game.steam_app_id}): "
                        f"hybrid {game.hybrid_score:.4f}"
                    )
    finally:
        try:
            await client.close()
        finally:
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

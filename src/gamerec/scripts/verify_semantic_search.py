import asyncio

from gamerec.integrations.qdrant import get_qdrant_client
from gamerec.ml.embedding_model import embed_search_query, load_embedding_model
from gamerec.services.semantic_search import search_games_by_embedding

QUERIES = (
    "Open-world RPG with deep character progression and a strong story",
    "Cooperative survival crafting game with challenging bosses",
    "Fast-paced first-person shooter with intense combat",
)


async def main() -> None:
    model = load_embedding_model()
    client = get_qdrant_client()
    try:
        for query in QUERIES:
            embedding = embed_search_query(query, model)
            results = await search_games_by_embedding(client, embedding, top_k=10)
            print(f"Query: {query}")
            for game in results:
                print(
                    f"  {game['name']} | Steam App ID: {game['steam_app_id']} | "
                    f"Similarity: {game['score']:.4f}"
                )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())

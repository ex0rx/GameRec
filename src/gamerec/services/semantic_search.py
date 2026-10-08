from qdrant_client import AsyncQdrantClient

from gamerec.core.config import settings
from gamerec.services.vector_store import SimilarGame, validate_game_collection

MAX_SEARCH_RESULTS = 1000


async def search_games_by_embedding(
    client: AsyncQdrantClient,
    query_embedding: list[float],
    top_k: int = 20,
) -> list[SimilarGame]:
    """Return Qdrant's highest scoring games in its original order."""
    if type(top_k) is not int or not 1 <= top_k <= MAX_SEARCH_RESULTS:
        raise ValueError(f"top_k must be an integer between 1 and {MAX_SEARCH_RESULTS}")
    if len(query_embedding) != settings.embeddings_vector_size:
        raise ValueError(f"Expected {settings.embeddings_vector_size} dimensions")

    await validate_game_collection(client)
    response = await client.query_points(
        collection_name=settings.qdrant_game_collection,
        query=query_embedding,
        limit=top_k,
        with_payload=["name"],
        with_vectors=False,
    )
    results: list[SimilarGame] = []
    for point in response.points:
        name = (point.payload or {}).get("name")
        results.append(
            {
                "steam_app_id": int(point.id),
                "name": name if isinstance(name, str) else "",
                "score": point.score,
            }
        )
    return results

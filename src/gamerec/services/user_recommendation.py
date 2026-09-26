from qdrant_client import AsyncQdrantClient

from gamerec.core.config import settings


async def get_user_recommendation_candidates(
    client: AsyncQdrantClient,
    user_profile_vector: list[float],
    candidate_k: int = 10,
) -> list[dict]:
    """
    Retrieve a list of candidate games for recommendation based on the user's profile vector.

    Args:
        client (AsyncQdrantClient): The Qdrant client instance.
        user_profile_vector (list[float]): The user's profile vector.
        candidate_k (int): The number of candidate games to retrieve.

    Returns:
        list[dict]: A list of candidate games with their details.
    """
    if candidate_k <= 0:
        raise ValueError("candidate_k must be a non-zero positive integer.")

    response = await client.query_points(
        collection_name=settings.qdrant_game_collection,
        query=user_profile_vector,
        limit=candidate_k,
        with_payload=True,
        with_vectors=False,
    )

    return [
        {
            "steam_app_id": int(point.id),
            "name": point.payload.get("name") if point.payload else None,
            "score": point.score,
        }
        for point in response.points
    ]


def filter_owned_games(
    candidates: list[dict],
    user_steam_app_ids: set[int],
) -> list[dict]:
    """
    Filter out games that the user already owns from the list of candidate games.

    Args:
        candidates (list[dict]): The list of candidate games.
        user_steam_app_ids (set[int]): The set of Steam App IDs that the user already owns.

    Returns:
        list[dict]: A filtered list of candidate games that the user does not own.
    """
    return [
        candidate
        for candidate in candidates
        if candidate["steam_app_id"] not in user_steam_app_ids
    ]

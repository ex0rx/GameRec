from sentence_transformers import SentenceTransformer

from gamerec.core.config import settings


def load_embedding_model() -> SentenceTransformer:
    """Load the model and revision used for stored game embeddings."""
    return SentenceTransformer(
        settings.embeddings_model_name,
        revision=settings.embeddings_model_revision,
    )


def embed_search_query(query: str, model: SentenceTransformer) -> list[float]:
    """Encode a search query in the same vector space as game embeddings."""
    query = query.strip()
    if not query:
        raise ValueError("Search query must not be empty")

    vector = model.encode(query, normalize_embeddings=True)
    embedding = [float(value) for value in vector]
    if len(embedding) != settings.embeddings_vector_size:
        raise ValueError(f"Expected {settings.embeddings_vector_size} dimensions")
    return embedding

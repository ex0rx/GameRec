from pydantic import BaseModel, ConfigDict, Field


class SearchEmbeddingRequest(BaseModel):
    query: str


class SearchEmbeddingResponse(BaseModel):
    embedding: list[float]


class SearchGameResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    steam_app_id: int
    name: str
    similarity_score: float
    popularity_score: float
    review_quality: float
    hybrid_score: float


class SearchGamesResponse(BaseModel):
    query: str
    total: int = Field(description="Number of returned games, not catalogue matches")
    limit: int
    games: list[SearchGameResponse]

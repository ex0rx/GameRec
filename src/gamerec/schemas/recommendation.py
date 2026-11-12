"""Read-only personalised recommendation API contracts."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    steamid64: str = Field(min_length=1, max_length=20, pattern=r"^[1-9][0-9]*$")
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("steamid64")
    @classmethod
    def validate_unsigned_id(cls, value: str) -> str:
        if int(value) > 2**64 - 1:
            raise ValueError(
                "steamid64 must represent a positive unsigned 64-bit integer"
            )
        return value


class RecommendedGameResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, allow_inf_nan=False)

    steam_app_id: int
    name: str | None
    similarity_score: float
    popularity_score: float
    review_quality: float
    hybrid_score: float
    affinity_score: float
    recency_score: float


class RecommendationResponse(BaseModel):
    total: int = Field(
        description="Number of returned recommendations, not catalogue matches"
    )
    limit: int
    games: list[RecommendedGameResponse]

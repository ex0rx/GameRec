"""Stored game evidence, model output and on-demand explanation API schemas."""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


class SearchExplanationContext(BaseModel):
    steam_app_id: int
    name: str
    search_query: str
    description: str | None = None
    genres: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    release_date: date | None = None

    @field_validator("name", "search_query")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("search_query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        if not value:
            raise ValueError("search_query must not be blank")
        return value

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return (" ".join(value.split()) or None) if value is not None else None

    @field_validator("genres", "categories")
    @classmethod
    def normalize_labels(cls, values: list[str]) -> list[str]:
        return [" ".join(value.split()) for value in values if value.strip()]

    @computed_field
    @property
    def insufficient_descriptive_evidence(self) -> bool:
        """Presence check only; it does not establish a match to the query."""
        return not (self.description or self.genres or self.categories)


class SearchExplanation(BaseModel):
    """Validated output shape; validation does not establish factual correctness."""

    model_config = ConfigDict(extra="forbid", strict=True)

    matching_features: list[str] = Field(
        description="Short relevant feature list consistent with the explanation; metadata, reasonable inference and well-established game knowledge are allowed",
    )
    explanation: str = Field(
        min_length=1,
        description="1–2 natural recommendation sentences, about 30–60 words; prioritise metadata, allow reasonable inference and established game knowledge, omit uncertain specifics",
    )

    @field_validator("explanation")
    @classmethod
    def validate_explanation(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("explanation must not be blank")
        return value

    @field_validator("matching_features")
    @classmethod
    def validate_features(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("matching_features must contain non-blank strings")
        return [value.strip() for value in values]


class SearchExplanationRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    steam_app_id: int = Field(gt=0)
    search_query: str = Field(min_length=1, max_length=500)

    @field_validator("search_query", mode="before")
    @classmethod
    def trim_query(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class SearchExplanationResponse(SearchExplanation):
    steam_app_id: int = Field(gt=0)
    game_name: str = Field(min_length=1)

"""Stored game evidence for a future search explanation."""

from datetime import date

from pydantic import BaseModel, Field, computed_field, field_validator


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

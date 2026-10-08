"""Explicit metadata constraints for natural-language search candidates."""

from dataclasses import dataclass

from gamerec.services.hybrid_user_recommendation import CandidateRankingMetadata


@dataclass(frozen=True)
class SearchFilters:
    genres: list[str] | None = None
    categories: list[str] | None = None
    release_year_from: int | None = None
    release_year_to: int | None = None
    min_reviews: int | None = None

    def __post_init__(self) -> None:
        validate_search_filters(self)


def validate_search_filters(filters: SearchFilters) -> None:
    for field_name in ("genres", "categories"):
        values = getattr(filters, field_name)
        if values is not None and (
            not isinstance(values, list)
            or any(not isinstance(value, str) or not value.strip() for value in values)
        ):
            raise ValueError(f"{field_name} must contain non-blank strings")

    for field_name in ("release_year_from", "release_year_to"):
        year = getattr(filters, field_name)
        if year is not None and (type(year) is not int or not 1 <= year <= 9999):
            raise ValueError(f"{field_name} must be a year from 1 to 9999")
    if (
        filters.release_year_from is not None
        and filters.release_year_to is not None
        and filters.release_year_from > filters.release_year_to
    ):
        raise ValueError("release_year_from must not exceed release_year_to")
    if filters.min_reviews is not None and (
        type(filters.min_reviews) is not int or filters.min_reviews < 0
    ):
        raise ValueError("min_reviews must be a non-negative integer")


def matches_search_filters(
    metadata: CandidateRankingMetadata,
    filters: SearchFilters,
) -> bool:
    """Use OR within each label group and AND across supplied constraints."""
    for labels, requested in (
        (metadata.genres, filters.genres),
        (metadata.categories, filters.categories),
    ):
        if requested:
            wanted = {value.strip().casefold() for value in requested}
            if not any(
                isinstance(value, str) and value.strip().casefold() in wanted
                for value in labels or []
            ):
                return False

    if filters.release_year_from is not None or filters.release_year_to is not None:
        if metadata.release_date is None:
            return False
        year = metadata.release_date.year
        if filters.release_year_from is not None and year < filters.release_year_from:
            return False
        if filters.release_year_to is not None and year > filters.release_year_to:
            return False

    return (
        filters.min_reviews is None
        or (metadata.total_reviews or 0) >= filters.min_reviews
    )

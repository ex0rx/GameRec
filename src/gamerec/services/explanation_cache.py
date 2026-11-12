"""Cache validated explanations; cache faults never replace inference errors."""

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Literal

from fastapi import Request
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from gamerec.core.config import settings
from gamerec.integrations.ollama import OLLAMA_GENERATION_OPTIONS
from gamerec.schemas.explanation import SearchExplanation, SearchExplanationContext
from gamerec.services.search_explanation import build_search_explanation_messages

logger = logging.getLogger(__name__)
CacheStatus = Literal["HIT", "MISS", "BYPASS"]


@dataclass(frozen=True)
class ExplanationCache:
    client: Redis
    ttl_seconds: int
    version: str

    @property
    def prefix(self) -> str:
        return f"gamerec:explanations:{self.version}:"

    def key(self, context: SearchExplanationContext) -> str:
        fingerprint = {
            "context": context.model_dump(mode="json"),
            "messages": build_search_explanation_messages(context),
            "schema": SearchExplanation.model_json_schema(),
            "server": settings.ollama_base_url,
            "model": settings.ollama_model,
            "options": OLLAMA_GENERATION_OPTIONS,
        }
        encoded = json.dumps(
            fingerprint, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        return f"{self.prefix}{context.steam_app_id}:{digest}"

    async def get(self, key: str) -> tuple[SearchExplanation | None, CacheStatus]:
        try:
            value = await self.client.get(key)
        except RedisError:
            logger.warning("Explanation cache read unavailable")
            return None, "BYPASS"
        except UnicodeError:
            logger.warning("Invalid explanation cache encoding")
            return None, "MISS"
        if value is None:
            return None, "MISS"
        try:
            return SearchExplanation.model_validate_json(value), "HIT"
        except ValidationError:
            logger.warning("Invalid explanation cache entry")
            return None, "MISS"

    async def put(self, key: str, explanation: SearchExplanation) -> None:
        try:
            await self.client.set(
                key, explanation.model_dump_json(), ex=self.ttl_seconds
            )
        except RedisError:
            logger.warning("Explanation cache write unavailable")


async def get_explanation_cache(request: Request) -> ExplanationCache | None:
    return getattr(request.app.state, "explanation_cache", None)

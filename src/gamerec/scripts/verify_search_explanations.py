"""Manually inspect local explanations using real stored PostgreSQL metadata."""

import asyncio
import json
import time

from gamerec.db import SessionLocal, engine
from gamerec.integrations.ollama import create_ollama_client
from gamerec.schemas.explanation import SearchExplanationContext
from gamerec.services.explanation_context import (
    format_search_explanation_context,
    get_search_explanation_context,
)
from gamerec.services.search_explanation import generate_search_explanation

# Each alternate is used only if the preferred game has no descriptive evidence.
EXAMPLES = (
    ((108600, 892970), "Cooperative survival crafting game with challenging bosses"),
    ((1091500, 292030), "Story-driven RPG with deep character progression"),
    ((2310, 379720), "Fast-paced first-person shooter with intense combat"),
)


async def main() -> None:
    try:
        contexts: list[tuple[SearchExplanationContext, float]] = []
        async with SessionLocal() as db:
            for app_ids, query in EXAMPLES:
                start = time.perf_counter()
                for app_id in app_ids:
                    context = await get_search_explanation_context(db, app_id, query)
                    if (
                        context is not None
                        and not context.insufficient_descriptive_evidence
                    ):
                        if app_id != app_ids[0]:
                            print(
                                f"Substitution: {app_ids[0]} lacks usable metadata; using {context.name} ({app_id})"
                            )
                        contexts.append((context, (time.perf_counter() - start) * 1000))
                        break
                else:
                    raise ValueError(f"No usable stored metadata for app IDs {app_ids}")

        async with create_ollama_client() as client:
            for context, metadata_ms in contexts:
                print("\nLLM metadata input:")
                print(
                    format_search_explanation_context(context, include_query=False),
                    flush=True,
                )
                start = time.perf_counter()
                generated = await generate_search_explanation(context, client)
                generation_ms = (time.perf_counter() - start) * 1000
                print(
                    json.dumps(
                        {
                            "game": context.name,
                            "steam_app_id": context.steam_app_id,
                            "original_query": context.search_query,
                            "metadata_retrieval_ms": metadata_ms,
                            "generation_ms": generation_ms,
                            "explanation": generated.explanation.model_dump(),
                            "ollama": (
                                generated.ollama_response.model_dump(
                                    exclude={"message"}
                                )
                                if generated.ollama_response
                                else None
                            ),
                        },
                        indent=2,
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

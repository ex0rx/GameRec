"""Internal HTTP interface for query inference in the embeddings container."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from gamerec.ml.embedding_model import embed_search_query, load_embedding_model
from gamerec.schemas.search import SearchEmbeddingRequest, SearchEmbeddingResponse


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.model = await asyncio.to_thread(load_embedding_model)
    app.state.inference_lock = asyncio.Lock()
    try:
        yield
    finally:
        del app.state.model
        del app.state.inference_lock


app = FastAPI(lifespan=lifespan)


@app.post("/embed/search", response_model=SearchEmbeddingResponse)
async def embed_search(
    payload: SearchEmbeddingRequest, request: Request
) -> SearchEmbeddingResponse:
    try:
        async with request.app.state.inference_lock:
            embedding = await asyncio.to_thread(
                embed_search_query, payload.query, request.app.state.model
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SearchEmbeddingResponse(embedding=embedding)

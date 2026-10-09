"""Native Ollama chat requests over a caller-owned asynchronous HTTP client."""

from typing import Literal

import httpx
from pydantic import BaseModel, Field, field_validator

from gamerec.core.config import settings


class OllamaUnavailableError(RuntimeError):
    """A transport or server failure; no substitute explanation is generated."""


class OllamaTimeoutError(OllamaUnavailableError):
    """The model request exceeded its configured connection or inference timeout."""


class OllamaModelNotFoundError(OllamaUnavailableError):
    """The configured model must be pulled into the Ollama server."""


class OllamaResponseError(RuntimeError):
    """Ollama returned incomplete or invalid structured output."""


class OllamaMessage(BaseModel):
    role: Literal["assistant"]
    content: str

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Ollama message content must not be blank")
        return value


class OllamaChatResponse(BaseModel):
    model: str
    message: OllamaMessage
    done: Literal[True]
    done_reason: str | None = None
    total_duration: int | None = Field(default=None, ge=0)
    load_duration: int | None = Field(default=None, ge=0)
    prompt_eval_count: int | None = Field(default=None, ge=0)
    prompt_eval_duration: int | None = Field(default=None, ge=0)
    eval_count: int | None = Field(default=None, ge=0)
    eval_duration: int | None = Field(default=None, ge=0)


def create_ollama_client() -> httpx.AsyncClient:
    """Use as an async context manager to reuse connections and ensure cleanup."""
    return httpx.AsyncClient(
        base_url=settings.ollama_base_url,
        timeout=httpx.Timeout(
            settings.ollama_generation_timeout,
            connect=settings.ollama_connect_timeout,
        ),
    )


async def chat_with_ollama(
    client: httpx.AsyncClient,
    messages: list[dict[str, str]],
    response_schema: dict[str, object],
) -> OllamaChatResponse:
    """The caller owns and closes client; requests are non-streaming and not retried."""
    try:
        response = await client.post(
            "/api/chat",
            json={
                "model": settings.ollama_model,
                "messages": messages,
                "stream": False,
                "format": response_schema,
                "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 512},
                "keep_alive": "5m",
            },
        )
    except httpx.TimeoutException as exc:
        raise OllamaTimeoutError("Ollama request timed out") from exc
    except httpx.RequestError as exc:
        raise OllamaUnavailableError("Could not connect to Ollama") from exc

    if response.status_code == 404:
        try:
            error = response.json().get("error", "")
        except (ValueError, AttributeError):
            error = ""
        if (
            isinstance(error, str)
            and "model" in error.lower()
            and "not found" in error.lower()
        ):
            raise OllamaModelNotFoundError("Configured Ollama model is not installed")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise OllamaUnavailableError(
            f"Ollama returned HTTP {response.status_code}"
        ) from exc

    try:
        result = OllamaChatResponse.model_validate(response.json())
    except ValueError as exc:
        raise OllamaResponseError("Ollama returned a malformed chat response") from exc
    if result.done_reason == "length":
        raise OllamaResponseError("Ollama output reached its token limit")
    return result

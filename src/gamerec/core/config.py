# src/gamerec/core/config.py

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "GameRec"
    environment: str = "development"
    database_url: str
    steam_api_key: str
    steamid64_test: str | None = None
    manual_library_sync_enabled: bool = False
    user_preference_writes_enabled: bool = False
    manual_library_sync_cooldown_seconds: int = Field(default=60, gt=0)
    embeddings_model_name: str
    embeddings_model_revision: str
    embeddings_vector_size: int = 384
    embedding_service_url: str = "http://embeddings:8100"
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    qdrant_game_collection: str
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:4b-instruct"
    ollama_connect_timeout: float = Field(default=5.0, gt=0)
    ollama_generation_timeout: float = Field(default=180.0, gt=0)
    redis_url: str = "redis://redis:6379/0"
    redis_timeout_seconds: float = Field(default=0.1, gt=0)
    explanation_cache_enabled: bool = False
    explanation_cache_ttl_seconds: int = Field(default=3600, gt=0)
    explanation_cache_version: str = Field(
        default="v1", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

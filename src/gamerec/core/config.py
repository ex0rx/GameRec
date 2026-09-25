# src/gamerec/core/config.py

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "GameRec"
    environment: str = "development"
    database_url: str
    steam_api_key: str
    steamid64_test: str | None = None
    embeddings_model_name: str 
    embeddings_model_revision: str 
    embeddings_vector_size: int = 384
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    qdrant_game_collection: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
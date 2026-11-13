# src/gamerec/core/config.py

import re
from ipaddress import IPv6Address
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, model_validator
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
    auth_enabled: bool = False
    auth_public_base_url: str | None = None
    auth_frontend_origin: str | None = None
    auth_success_path: str = "/discover"
    auth_session_ttl_seconds: int = Field(default=604800, gt=0)
    auth_login_ttl_seconds: int = Field(default=600, gt=0)
    auth_cookie_secure: bool = True
    auth_session_cookie_name: str = "gamerec_session"
    auth_login_cookie_name: str = "gamerec_login"

    @model_validator(mode="after")
    def validate_auth(self) -> "Settings":
        if not self.auth_enabled:
            return self
        for name in (self.auth_session_cookie_name, self.auth_login_cookie_name):
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,47}", name) is None:
                raise ValueError(
                    "Authentication cookie base names must be simple identifiers"
                )
        if self.auth_session_cookie_name == self.auth_login_cookie_name:
            raise ValueError("Authentication cookies must have distinct names")
        if re.fullmatch(r"/(?:[A-Za-z0-9_-]+/?)*", self.auth_success_path) is None:
            raise ValueError("Authentication success path must be a local path")
        urls = []
        for value, origin_only in (
            (self.auth_public_base_url, False),
            (self.auth_frontend_origin, True),
        ):
            if (
                not value
                or not value.isascii()
                or any(ord(c) <= 32 or ord(c) == 127 for c in value)
            ):
                raise ValueError("Authentication requires explicit trusted URLs")
            try:
                parsed = urlsplit(value)
                port = parsed.port
            except ValueError:
                raise ValueError("Invalid authentication URL") from None
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or "\\" in value
                or re.fullmatch(r"(?:/[A-Za-z0-9_-]+)*/?", parsed.path) is None
                or (origin_only and parsed.path not in {"", "/"})
            ):
                raise ValueError("Invalid authentication URL")
            host = parsed.hostname.lower()
            if "%" in host or port == 0:
                raise ValueError("Invalid authentication host")
            if ":" in host:
                IPv6Address(host)
            elif (
                re.fullmatch(
                    r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*",
                    host,
                )
                is None
            ):
                raise ValueError("Invalid authentication host")
            authority = f"[{host}]" if ":" in host else host
            if port is not None and port != (443 if parsed.scheme == "https" else 80):
                authority += f":{port}"
            urls.append(
                (
                    parsed,
                    urlunsplit(
                        (parsed.scheme, authority, parsed.path.rstrip("/"), "", "")
                    ),
                )
            )
        if self.auth_cookie_secure:
            if any(parsed.scheme != "https" for parsed, _ in urls):
                raise ValueError(
                    "HTTP authentication requires explicit local insecure-cookie configuration"
                )
        elif self.environment != "development" or any(
            parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            for parsed, _ in urls
        ):
            raise ValueError(
                "Insecure authentication cookies are restricted to local HTTP development"
            )
        self.auth_public_base_url = urls[0][1]
        self.auth_frontend_origin = urls[1][1]
        return self

    @property
    def auth_callback_url(self) -> str:
        return f"{self.auth_public_base_url}/auth/steam/callback"

    @property
    def auth_realm(self) -> str:
        parsed = urlsplit(self.auth_public_base_url or "")
        return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))

    @property
    def auth_success_url(self) -> str:
        return f"{self.auth_frontend_origin}{self.auth_success_path}"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )


settings = Settings()

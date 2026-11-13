"""Authentication configuration fails closed without affecting disabled setups."""

import pytest
from pydantic import ValidationError

from gamerec.core.config import Settings


def configured(**overrides):
    values = {
        "database_url": "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        "steam_api_key": "synthetic-key",
        "embeddings_model_name": "synthetic-model",
        "embeddings_model_revision": "synthetic-revision",
        "qdrant_game_collection": "synthetic-games",
        "environment": "production",
        "auth_enabled": True,
        "auth_public_base_url": "https://games.example/api",
        "auth_frontend_origin": "https://games.example",
        "auth_success_path": "/discover",
        "auth_cookie_secure": True,
    }
    return Settings(_env_file=None, **(values | overrides))


def test_secure_fixed_lifetimes_and_cookie_names_are_defaults():
    settings = configured()
    assert settings.auth_session_ttl_seconds == 604800
    assert settings.auth_login_ttl_seconds == 600
    assert settings.auth_cookie_secure is True
    assert settings.auth_session_cookie_name == "gamerec_session"
    assert settings.auth_login_cookie_name == "gamerec_login"


def test_explicit_loopback_http_is_allowed_only_for_local_development():
    settings = configured(
        environment="development",
        auth_cookie_secure=False,
        auth_public_base_url="http://localhost:5173/api",
        auth_frontend_origin="http://localhost:5173",
    )
    assert settings.auth_cookie_secure is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"auth_public_base_url": None},
        {"auth_frontend_origin": None},
        {"auth_public_base_url": "http://games.example/api"},
        {"auth_frontend_origin": "http://games.example"},
        {"auth_cookie_secure": False},
        {"auth_public_base_url": "https://person:secret@games.example/api"},
        {"auth_public_base_url": "https://games.example/api?next=evil"},
        {"auth_public_base_url": "https://games.example/api#fragment"},
        {"auth_frontend_origin": "https://games.example/discover"},
        {"auth_frontend_origin": "*"},
        {"auth_frontend_origin": "null"},
        {"auth_success_path": "https://evil.example/"},
        {"auth_success_path": "//evil.example/"},
        {"auth_success_path": "/\\evil.example/"},
        {"auth_success_path": "/%2f%2fevil.example/"},
        {"auth_session_ttl_seconds": 0},
        {"auth_login_ttl_seconds": -1},
        {"auth_session_cookie_name": "bad;cookie"},
        {"auth_login_cookie_name": "bad cookie"},
        {"auth_session_cookie_name": "same", "auth_login_cookie_name": "same"},
    ],
)
def test_unsafe_or_incomplete_enabled_configuration_is_rejected(overrides):
    with pytest.raises(ValidationError):
        configured(**overrides)


@pytest.mark.parametrize("environment", ["production", "development"])
def test_insecure_non_loopback_deployment_is_never_allowed(environment):
    with pytest.raises(ValidationError):
        configured(
            environment=environment,
            auth_cookie_secure=False,
            auth_public_base_url="http://untrusted.example/api",
            auth_frontend_origin="http://untrusted.example",
        )


def test_disabled_auth_needs_no_public_origin_configuration():
    settings = configured(
        auth_enabled=False, auth_public_base_url=None, auth_frontend_origin=None
    )
    assert settings.auth_enabled is False


@pytest.mark.parametrize(
    "origin",
    [
        "https://*.example",
        "https://example..com",
        "https://games.example:0",
        "https://games.example:bad",
        "https://games.example/\x7f",
    ],
)
def test_invalid_host_or_port_is_rejected_without_echoing_configuration(origin):
    with pytest.raises(ValidationError) as error:
        configured(auth_frontend_origin=origin)
    assert origin not in str(error.value)

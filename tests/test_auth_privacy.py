"""Auth-only logging redaction and safe configuration diagnostics."""

import logging

import pytest

from gamerec.core.auth_privacy import AuthLogFilter, install_auth_log_filters


@pytest.mark.parametrize("path", ["/auth/steam/callback", "/api/auth/steam/callback"])
def test_uvicorn_callback_redaction_preserves_access_formatter_arguments(path):
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("client", "GET", path + "?openid.sig=synthetic-secret", "1.1", 400),
        None,
    )
    assert AuthLogFilter().filter(record)
    assert len(record.args) == 5
    assert "synthetic-secret" not in record.getMessage()
    assert "[redacted]" in record.getMessage()


@pytest.mark.parametrize("name", ["httpx", "httpcore.http11", "httpcore.http2"])
def test_steam_identity_urls_are_redacted(name):
    record = logging.LogRecord(
        name,
        logging.INFO,
        "",
        1,
        "GET %s",
        ("https://steamcommunity.com/openid/id/76561190000000001",),
        None,
    )
    AuthLogFilter().filter(record)
    assert "76561190000000001" not in record.getMessage()
    assert "/openid/id/[redacted]" in record.getMessage()


def test_filter_installation_does_not_accumulate_across_app_factories():
    install_auth_log_filters()
    install_auth_log_filters()
    logger = logging.getLogger("uvicorn.access")
    assert sum(isinstance(item, AuthLogFilter) for item in logger.filters) == 1


def test_ordinary_access_diagnostics_are_unchanged():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("client", "GET", "/search/games?query=adventure", "1.1", 200),
        None,
    )
    original = record.getMessage()
    AuthLogFilter().filter(record)
    assert record.getMessage() == original

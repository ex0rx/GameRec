"""Owned-library contract tests: synthetic data, no Steam or database access."""

from unittest.mock import AsyncMock

import httpx
import pytest

from gamerec.integrations import steam


def test_normalise_visible_library_preserves_optional_data():
    games = [
        {"appid": 10, "name": "Example Game", "playtime_forever": 0},
        {"appid": 20},
    ]

    status, count, result = steam.normalise_owned_games(
        {"response": {"game_count": 2, "games": games}}
    )

    assert (status, count) == ("available", 2)
    assert result == [
        {
            "steam_app_id": 10,
            "name": "Example Game",
            "playtime_forever_minutes": 0,
            "playtime_2weeks_minutes": 0,
        },
        {
            "steam_app_id": 20,
            "name": None,
            "playtime_forever_minutes": None,
            "playtime_2weeks_minutes": 0,
        },
    ]
    # Normalisation must not mutate the source response.
    assert games == [
        {"appid": 10, "name": "Example Game", "playtime_forever": 0},
        {"appid": 20},
    ]


@pytest.mark.parametrize(
    "fields, expected_lifetime, expected_recent",
    [
        pytest.param(
            {"playtime_forever": 240, "playtime_2weeks": 30},
            240,
            30,
            id="reported-values",
        ),
        pytest.param(
            {"playtime_forever": 0, "playtime_2weeks": 0},
            0,
            0,
            id="explicit-zero",
        ),
        # Omitted recent time is zero under our chosen application policy.
        pytest.param({"playtime_forever": 240}, 240, 0, id="omitted-recent"),
        pytest.param({"playtime_2weeks": 30}, None, 30, id="omitted-lifetime"),
        pytest.param(
            {"playtime_forever": None, "playtime_2weeks": 30},
            None,
            30,
            id="null-lifetime",
        ),
        pytest.param(
            {"playtime_forever": 240, "playtime_2weeks": None},
            240,
            None,
            id="null-recent",
        ),
    ],
)
def test_normalise_playtime_values(fields, expected_lifetime, expected_recent):
    payload = {"response": {"game_count": 1, "games": [{"appid": 10, **fields}]}}

    status, count, games = steam.normalise_owned_games(payload)

    assert (status, count) == ("available", 1)
    assert games[0]["playtime_forever_minutes"] == expected_lifetime
    assert games[0]["playtime_2weeks_minutes"] == expected_recent


@pytest.mark.parametrize("field", ["playtime_forever", "playtime_2weeks"])
@pytest.mark.parametrize("value", [True, -1, "30", 1.5])
def test_normalise_rejects_invalid_playtime(field, value):
    game = {"appid": 10, "playtime_forever": 240, "playtime_2weeks": 30}
    game[field] = value

    with pytest.raises(ValueError, match=field):
        steam.normalise_owned_games({"response": {"game_count": 1, "games": [game]}})


@pytest.mark.parametrize(
    "response",
    [{"game_count": 0}, {"game_count": 0, "games": []}],
    ids=["omitted-games", "explicit-empty-games"],
)
def test_normalise_explicitly_empty_library(response):
    assert steam.normalise_owned_games({"response": response}) == ("available", 0, [])


def test_normalise_unavailable_library_is_not_reported_as_empty():
    assert steam.normalise_owned_games({"response": {}}) == ("unavailable", None, [])


@pytest.mark.parametrize(
    "payload, expected_error",
    [
        pytest.param({}, TypeError, id="missing-envelope"),
        pytest.param({"response": None}, TypeError, id="null-envelope"),
        pytest.param({"response": []}, TypeError, id="list-envelope"),
        pytest.param({"response": {"games": []}}, ValueError, id="missing-count"),
        pytest.param(
            {"response": {"game_count": True}}, ValueError, id="boolean-count"
        ),
        pytest.param({"response": {"game_count": -1}}, ValueError, id="negative-count"),
        pytest.param({"response": {"game_count": "0"}}, ValueError, id="string-count"),
        pytest.param({"response": {"game_count": 1}}, ValueError, id="missing-games"),
        pytest.param(
            {"response": {"game_count": 0, "games": None}}, TypeError, id="null-games"
        ),
        pytest.param(
            {"response": {"game_count": 2, "games": []}},
            ValueError,
            id="count-mismatch",
        ),
        pytest.param(
            {"response": {"game_count": 1, "games": [None]}},
            TypeError,
            id="invalid-entry",
        ),
    ],
)
def test_normalise_rejects_malformed_library(payload, expected_error):
    with pytest.raises(expected_error):
        steam.normalise_owned_games(payload)


@pytest.mark.parametrize("appid", [None, True, 0, -1, "10"])
def test_normalise_rejects_invalid_app_id(appid):
    with pytest.raises(ValueError, match="appid"):
        steam.normalise_owned_games(
            {"response": {"game_count": 1, "games": [{"appid": appid}]}}
        )


def test_normalise_rejects_missing_app_id():
    with pytest.raises(ValueError, match="appid"):
        steam.normalise_owned_games({"response": {"game_count": 1, "games": [{}]}})


@pytest.fixture
def fake_steam_key(monkeypatch):
    # Never put the configured real API key into mocked requests or failures.
    monkeypatch.setattr(steam.settings, "steam_api_key", "test-key")


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake_steam_key")
@pytest.mark.parametrize("include_info, include_free", [(True, True), (False, False)])
@pytest.mark.parametrize(
    "payload, expected",
    [
        (
            {
                "response": {
                    "game_count": 1,
                    "games": [{"appid": 10, "playtime_forever": 60}],
                }
            },
            (
                "available",
                1,
                [
                    {
                        "steam_app_id": 10,
                        "name": None,
                        "playtime_forever_minutes": 60,
                        "playtime_2weeks_minutes": 0,
                    }
                ],
            ),
        ),
        ({"response": {"game_count": 0}}, ("available", 0, [])),
        ({"response": {}}, ("unavailable", None, [])),
    ],
    ids=["visible", "empty", "unavailable"],
)
async def test_fetch_and_normalise_library(
    include_info, include_free, payload, expected
):
    def handler(request):
        assert request.method == "GET"
        assert request.url.host == "api.steampowered.com"
        assert request.url.path == "/IPlayerService/GetOwnedGames/v1/"
        assert dict(request.url.params) == {
            "key": "test-key",
            "steamid": "synthetic-user",
            "include_appinfo": str(int(include_info)),
            "include_played_free_games": str(int(include_free)),
        }
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await steam.fetch_steam_owned_games(
            client, "synthetic-user", include_info, include_free
        )

    assert result == payload
    assert steam.normalise_owned_games(result) == expected


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake_steam_key")
@pytest.mark.parametrize("payload", [[], None, {}, "unexpected"])
async def test_fetch_rejects_invalid_envelope(payload):
    def handler(request):
        # Explicit null bytes avoid treating json=None as an absent body.
        if payload is None:
            return httpx.Response(200, content=b"null")
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await steam.fetch_steam_owned_games(client, "synthetic-user")


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake_steam_key")
@pytest.mark.parametrize("failure", [429, 503, "timeout"])
@pytest.mark.parametrize("recover", [True, False], ids=["recovery", "exhaustion"])
async def test_fetch_retries_transient_failures(monkeypatch, failure, recover):
    sleep = AsyncMock()
    monkeypatch.setattr(steam.asyncio, "sleep", sleep)
    attempts = 0
    payload = {"response": {"game_count": 0}}

    def handler(request):
        nonlocal attempts
        attempts += 1
        if recover and attempts == 2:
            return httpx.Response(200, json=payload)
        if failure == "timeout":
            raise httpx.ReadTimeout("Synthetic timeout", request=request)
        return httpx.Response(failure, headers={"Retry-After": "3"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        if recover:
            assert (
                await steam.fetch_steam_owned_games(client, "synthetic-user") == payload
            )
        else:
            error = httpx.ReadTimeout if failure == "timeout" else httpx.HTTPStatusError
            with pytest.raises(error):
                await steam.fetch_steam_owned_games(client, "synthetic-user")

    assert attempts == (2 if recover else 3)
    assert sleep.await_count == attempts - 1
    if failure == 429:
        assert all(call.args[0] >= 3 for call in sleep.await_args_list)


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake_steam_key")
async def test_fetch_does_not_retry_forbidden_response():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await steam.fetch_steam_owned_games(client, "synthetic-user")

    assert exc_info.value.response.status_code == 403
    assert attempts == 1

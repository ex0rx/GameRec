"""Real session/login transactions in UUID-isolated PostgreSQL databases.

Only Steam verification is mocked. No live provider, Steam import or model is used.
"""

import asyncio
import base64
import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import func, select, update

from gamerec.api import auth
from gamerec.core.config import settings
from gamerec.integrations.steam_openid import VerifiedIdentity
from gamerec.main import app
from gamerec.models import Game, User, UserGame, UserGamePreference
from gamerec.models.auth import AuthSession, SteamLoginTransaction

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]
USER = "76561190000000001"
ORIGIN = "http://localhost:5173"
NS = "http://specs.openid.net/auth/2.0"
PROVIDER = "https://steamcommunity.com/openid/login"
OLD = datetime(2000, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_public_base_url", ORIGIN + "/api")
    monkeypatch.setattr(settings, "auth_frontend_origin", ORIGIN)
    monkeypatch.setattr(settings, "auth_success_path", "/discover")
    monkeypatch.setattr(settings, "auth_cookie_secure", False)
    monkeypatch.setattr(settings, "auth_session_cookie_name", "gamerec_session")
    monkeypatch.setattr(settings, "auth_login_cookie_name", "gamerec_login")


@pytest.fixture
def verified(monkeypatch):
    calls = []

    async def verify(*args, **kwargs):
        parameters = kwargs.get("parameters", args[1] if len(args) > 1 else None)
        calls.append(kwargs.get("client", args[0] if args else None))
        return VerifiedIdentity(
            steamid64=USER,
            nonce=parameters["openid.response_nonce"],
            nonce_expires_at=datetime.now(UTC) + timedelta(minutes=12),
        )

    monkeypatch.setattr(auth, "verify_assertion", verify)
    return calls


async def begin(client, *, nonce=None):
    response = await client.get("/auth/steam/login", follow_redirects=False)
    assert response.status_code == 303
    redirect = parse_qs(urlsplit(response.headers["location"]).query)
    return_to = redirect["openid.return_to"][0]
    login_id = parse_qs(urlsplit(return_to).query)["login_id"][0]
    return {
        "login_id": login_id,
        "openid.ns": NS,
        "openid.mode": "id_res",
        "openid.op_endpoint": PROVIDER,
        "openid.return_to": return_to,
        "openid.claimed_id": f"https://steamcommunity.com/openid/id/{USER}",
        "openid.identity": f"https://steamcommunity.com/openid/id/{USER}",
        "openid.response_nonce": nonce
        or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") + login_id,
        "openid.assoc_handle": "synthetic-handle",
        "openid.signed": "op_endpoint,return_to,response_nonce,assoc_handle,claimed_id,identity",
        "openid.sig": base64.b64encode(b"synthetic-signature!!").decode(),
    }


async def complete(client, *, nonce=None):
    parameters = await begin(client, nonce=nonce)
    response = await client.get(
        "/auth/steam/callback", params=parameters, follow_redirects=False
    )
    assert response.status_code == 303
    return parameters, response


async def test_login_hashes_tokens_reuses_user_and_preserves_existing_data(
    pg_client, pg_sessions, verified
):
    async with pg_sessions() as db:
        db.add(User(steamid64=USER, library_last_synced_at=OLD))
        db.add(Game(steam_app_id=10, name="Synthetic game"))
        await db.commit()
        db.add(UserGame(steamid64=USER, steam_app_id=10, playtime_forever_minutes=123))
        db.add(UserGamePreference(steamid64=USER, steam_app_id=10, preference="liked"))
        await db.commit()
    parameters = await begin(pg_client)
    raw_login = pg_client.cookies.get("gamerec_login")
    async with pg_sessions() as db:
        transaction = await db.get(SteamLoginTransaction, parameters["login_id"])
        assert (
            transaction.browser_token_hash
            == hashlib.sha256(raw_login.encode()).hexdigest()
        )
        assert transaction.browser_token_hash != raw_login
        assert transaction.consumed_at is None
    response = await pg_client.get("/auth/steam/callback", params=parameters)
    assert response.status_code == 303
    assert response.headers["location"] == ORIGIN + "/discover"
    raw_session = pg_client.cookies.get("gamerec_session")
    assert len(raw_session) == 43
    assert pg_client.cookies.get("gamerec_login") is None
    async with pg_sessions() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        session = await db.get(
            AuthSession, hashlib.sha256(raw_session.encode()).hexdigest()
        )
        assert session.steamid64 == USER
        assert session.token_hash != raw_session
        assert (
            abs((session.expires_at - session.created_at).total_seconds() - 604800) < 1
        )
        transaction = await db.get(SteamLoginTransaction, parameters["login_id"])
        assert transaction.consumed_at is not None
        assert (
            transaction.nonce_hash
            == hashlib.sha256(parameters["openid.response_nonce"].encode()).hexdigest()
        )
        assert transaction.nonce_expires_at > datetime.now(UTC)
        assert (await db.get(User, USER)).library_last_synced_at == OLD
        assert (await db.get(UserGame, (USER, 10))).playtime_forever_minutes == 123
        assert (await db.get(UserGamePreference, (USER, 10))).preference == "liked"
    me = await pg_client.get("/auth/me")
    assert me.status_code == 200
    assert me.json() == {"steamid64": USER}
    assert raw_session not in me.text
    assert len(verified) == 1
    assert verified[0].is_closed


async def test_new_user_created_without_ownership_or_preferences(
    pg_client, pg_sessions, verified
):
    await complete(pg_client)
    async with pg_sessions() as db:
        assert await db.get(User, USER) is not None
        for model in (UserGame, UserGamePreference, Game):
            assert await db.scalar(select(func.count()).select_from(model)) == 0


async def test_logout_is_idempotent_and_revokes_only_current_session(
    pg_client, pg_sessions, verified
):
    await complete(pg_client)
    token = pg_client.cookies.get("gamerec_session")
    rejected = await pg_client.post(
        "/auth/logout", headers={"Origin": "https://evil.example"}
    )
    assert rejected.status_code == 403
    assert (await pg_client.get("/auth/me")).status_code == 200
    response = await pg_client.post("/auth/logout", headers={"Origin": ORIGIN})
    assert response.status_code == 204 and response.content == b""
    assert (await pg_client.get("/auth/me")).status_code == 401
    assert (
        await pg_client.post("/auth/logout", headers={"Origin": ORIGIN})
    ).status_code == 204
    async with pg_sessions() as db:
        assert (
            await db.get(AuthSession, hashlib.sha256(token.encode()).hexdigest())
            is None
        )
        assert await db.get(User, USER) is not None


async def test_new_login_rotates_session_and_old_cookie_no_longer_authenticates(
    pg_client, pg_sessions, verified
):
    await complete(pg_client)
    old = pg_client.cookies.get("gamerec_session")
    await complete(pg_client)
    new = pg_client.cookies.get("gamerec_session")
    assert new != old
    async with pg_sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AuthSession)) == 1
    response = await pg_client.get(
        "/auth/me", headers={"Cookie": f"gamerec_session={old}"}
    )
    assert response.status_code == 401
    assert (await pg_client.get("/auth/me")).status_code == 200


async def test_expired_and_deleted_sessions_are_unauthenticated(
    pg_client, pg_sessions, verified
):
    await complete(pg_client)
    token = pg_client.cookies.get("gamerec_session")
    async with pg_sessions() as db:
        session = await db.get(AuthSession, hashlib.sha256(token.encode()).hexdigest())
        session.created_at = OLD
        session.expires_at = OLD + timedelta(days=7)
        await db.commit()
    assert (await pg_client.get("/auth/me")).status_code == 401
    pg_client.cookies.set("gamerec_session", "x" * 43)
    assert (await pg_client.get("/auth/me")).status_code == 401


async def test_transaction_browser_binding_expiry_and_replay(
    pg_client, pg_sessions, verified
):
    parameters = await begin(pg_client)
    cookie = pg_client.cookies.get("gamerec_login")
    response = await pg_client.get(
        "/auth/steam/callback",
        params=parameters,
        headers={"Cookie": "gamerec_login=" + "x" * 43},
    )
    assert response.status_code == 400
    assert verified == []
    # A mismatched browser must not consume the legitimate browser's transaction.
    assert pg_client.cookies.get("gamerec_login") == cookie
    response = await pg_client.get("/auth/steam/callback", params=parameters)
    assert response.status_code == 303
    response = await pg_client.get(
        "/auth/steam/callback",
        params=parameters,
        headers={"Cookie": f"gamerec_login={cookie}"},
    )
    assert response.status_code == 400
    assert len(verified) == 1
    expired = await begin(pg_client)
    async with pg_sessions() as db:
        await db.execute(
            update(SteamLoginTransaction)
            .where(SteamLoginTransaction.login_id == expired["login_id"])
            .values(created_at=OLD, expires_at=OLD + timedelta(minutes=10))
        )
        await db.commit()
    assert (
        await pg_client.get("/auth/steam/callback", params=expired)
    ).status_code == 400
    assert len(verified) == 1


async def test_nonce_reuse_is_rejected_even_across_different_transactions(
    pg_client, pg_sessions, verified
):
    nonce = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") + "same-nonce"
    await complete(pg_client, nonce=nonce)
    second = await begin(pg_client, nonce=nonce)
    response = await pg_client.get("/auth/steam/callback", params=second)
    assert response.status_code == 400
    async with pg_sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AuthSession)) == 1
        assert await db.scalar(select(func.count()).select_from(User)) == 1


async def test_concurrent_first_logins_create_one_user(
    pg_client, pg_sessions, verified, monkeypatch
):
    second_transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=second_transport, base_url="http://test"
    ) as other:
        parameters = await begin(pg_client)
        second = await begin(other)
        first, last = await asyncio.gather(
            pg_client.get("/auth/steam/callback", params=parameters),
            other.get("/auth/steam/callback", params=second),
        )
        assert first.status_code == last.status_code == 303
    async with pg_sessions() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        assert await db.scalar(select(func.count()).select_from(AuthSession)) == 2
    assert len(verified) == 2


async def test_concurrent_same_callback_consumes_once(pg_client, pg_sessions, verified):
    parameters = await begin(pg_client)
    cookie = pg_client.cookies.get("gamerec_login")
    responses = await asyncio.gather(
        *[
            pg_client.get(
                "/auth/steam/callback",
                params=parameters,
                headers={"Cookie": f"gamerec_login={cookie}"},
            )
            for _ in range(2)
        ]
    )
    assert sorted(response.status_code for response in responses) == [303, 400]
    assert len(verified) == 1
    async with pg_sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AuthSession)) == 1


async def test_cleanup_is_bounded_and_retains_used_nonce_until_its_window_closes(
    pg_client, pg_sessions
):
    from gamerec.core.auth_security import token_hash

    async with pg_sessions() as db:
        db.add(User(steamid64=USER))
        await db.commit()
        db.add_all(
            AuthSession(
                token_hash=token_hash(f"expired-{i}"),
                steamid64=USER,
                created_at=OLD,
                expires_at=OLD + timedelta(days=7),
            )
            for i in range(101)
        )
        db.add_all(
            [
                SteamLoginTransaction(
                    login_id="p" * 43,
                    browser_token_hash="a" * 64,
                    expected_return_to="https://example.test",
                    created_at=OLD,
                    expires_at=OLD + timedelta(minutes=10),
                    consumed_at=OLD,
                    nonce_hash="b" * 64,
                    nonce_expires_at=datetime.now(UTC) + timedelta(minutes=12),
                ),
                SteamLoginTransaction(
                    login_id="q" * 43,
                    browser_token_hash="c" * 64,
                    expected_return_to="https://example.test",
                    created_at=OLD,
                    expires_at=OLD + timedelta(minutes=10),
                    consumed_at=OLD,
                    nonce_hash="d" * 64,
                    nonce_expires_at=OLD + timedelta(minutes=12),
                ),
                SteamLoginTransaction(
                    login_id="r" * 43,
                    browser_token_hash="e" * 64,
                    expected_return_to="https://example.test",
                    created_at=OLD,
                    expires_at=OLD + timedelta(minutes=10),
                ),
            ]
        )
        await db.commit()
    await begin(pg_client)
    async with pg_sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AuthSession)) == 1
        assert await db.get(SteamLoginTransaction, "p" * 43) is not None
        assert await db.get(SteamLoginTransaction, "q" * 43) is None
        assert await db.get(SteamLoginTransaction, "r" * 43) is None


async def test_failed_verification_consumes_transaction_and_never_retries(
    pg_client, pg_sessions, monkeypatch
):
    from gamerec.integrations.steam_openid import OpenIDError

    calls = []

    async def failed(*args):
        calls.append(args[0])
        raise OpenIDError(503)

    monkeypatch.setattr(auth, "verify_assertion", failed)
    parameters = await begin(pg_client)
    cookie = pg_client.cookies.get("gamerec_login")
    assert (
        await pg_client.get("/auth/steam/callback", params=parameters)
    ).status_code == 503
    assert (
        await pg_client.get(
            "/auth/steam/callback",
            params=parameters,
            headers={"Cookie": f"gamerec_login={cookie}"},
        )
    ).status_code == 400
    assert len(calls) == 1 and calls[0].is_closed
    async with pg_sessions() as db:
        transaction = await db.get(SteamLoginTransaction, parameters["login_id"])
        assert transaction.consumed_at is not None and transaction.nonce_hash is None
        assert await db.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert await db.get(User, USER) is None


async def test_actual_verifier_and_database_with_mocked_steam_http(
    pg_client, pg_sessions, monkeypatch
):
    from tests.test_steam_openid import VALID_DIRECT, xrds

    original_client = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200, content=VALID_DIRECT if request.method == "POST" else xrds()
        )

    monkeypatch.setattr(
        auth.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(respond), **kwargs
        ),
    )
    await complete(pg_client)
    assert [request.method for request in requests] == ["POST", "GET"]
    assert parse_qs(requests[0].content.decode())["openid.mode"] == [
        "check_authentication"
    ]
    assert (await pg_client.get("/auth/me")).json() == {"steamid64": USER}

"""PostgreSQL login claims, replay protection and opaque session persistence."""

import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.core.auth_security import AuthError, token_hash, valid_token
from gamerec.core.config import settings
from gamerec.integrations.steam_openid import VerifiedIdentity
from gamerec.models.auth import AuthSession, SteamLoginTransaction
from gamerec.models.user import User

CLEANUP_BATCH_SIZE = 100


async def cleanup_expired_auth(db: AsyncSession, now: datetime) -> None:
    expired_sessions = (
        select(AuthSession.token_hash)
        .where(AuthSession.expires_at <= now)
        .limit(CLEANUP_BATCH_SIZE)
    )
    await db.execute(
        delete(AuthSession).where(AuthSession.token_hash.in_(expired_sessions))
    )
    expired_logins = (
        select(SteamLoginTransaction.login_id)
        .where(
            SteamLoginTransaction.expires_at <= now,
            or_(
                SteamLoginTransaction.nonce_expires_at.is_(None),
                SteamLoginTransaction.nonce_expires_at <= now,
            ),
        )
        .limit(CLEANUP_BATCH_SIZE)
    )
    await db.execute(
        delete(SteamLoginTransaction).where(
            SteamLoginTransaction.login_id.in_(expired_logins)
        )
    )


async def create_login_transaction(
    db: AsyncSession,
) -> tuple[SteamLoginTransaction, str]:
    now = datetime.now(UTC)
    public_id, private_cookie = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    transaction = SteamLoginTransaction(
        login_id=public_id,
        browser_token_hash=token_hash(private_cookie),
        expected_return_to=settings.auth_callback_url
        + "?"
        + urlencode({"login_id": public_id}),
        created_at=now,
        expires_at=now + timedelta(seconds=settings.auth_login_ttl_seconds),
    )
    async with db.begin():
        await cleanup_expired_auth(db, now)
        db.add(transaction)
    return transaction, private_cookie


async def consume_login_transaction(
    db: AsyncSession, login_id: str, browser_secret: str | None
) -> SteamLoginTransaction:
    if not valid_token(login_id) or not valid_token(browser_secret):
        raise AuthError()
    now = datetime.now(UTC)
    async with db.begin():
        transaction = await db.scalar(
            update(SteamLoginTransaction)
            .where(
                SteamLoginTransaction.login_id == login_id,
                SteamLoginTransaction.browser_token_hash == token_hash(browser_secret),
                SteamLoginTransaction.consumed_at.is_(None),
                SteamLoginTransaction.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(SteamLoginTransaction)
        )
        if transaction is None:
            raise AuthError()
    return transaction


async def establish_session(
    db: AsyncSession,
    transaction: SteamLoginTransaction,
    identity: VerifiedIdentity,
    previous_token: str | None,
) -> str:
    now = datetime.now(UTC)
    raw_token = secrets.token_urlsafe(32)
    try:
        async with db.begin():
            # The claim was committed before remote verification. Retain its nonce
            # marker through the acceptance window even after session logout.
            claimed_id = await db.scalar(
                update(SteamLoginTransaction)
                .where(
                    SteamLoginTransaction.login_id == transaction.login_id,
                    SteamLoginTransaction.consumed_at.is_not(None),
                    SteamLoginTransaction.nonce_hash.is_(None),
                    SteamLoginTransaction.expires_at > now,
                )
                .values(
                    nonce_hash=token_hash(identity.nonce),
                    nonce_expires_at=identity.nonce_expires_at,
                )
                .returning(SteamLoginTransaction.login_id)
            )
            if claimed_id is None:
                raise AuthError()
            await db.execute(
                insert(User)
                .values(steamid64=identity.steamid64)
                .on_conflict_do_nothing(index_elements=[User.steamid64])
            )
            if valid_token(previous_token):
                await db.execute(
                    delete(AuthSession).where(
                        AuthSession.token_hash == token_hash(previous_token)
                    )
                )
            db.add(
                AuthSession(
                    token_hash=token_hash(raw_token),
                    steamid64=identity.steamid64,
                    created_at=now,
                    expires_at=now
                    + timedelta(seconds=settings.auth_session_ttl_seconds),
                )
            )
    except IntegrityError as exc:
        cause = getattr(exc.orig, "__cause__", None)
        constraint = getattr(cause, "constraint_name", None) or getattr(
            exc.orig, "constraint_name", None
        )
        if constraint == "uq_steam_login_nonce_hash":
            raise AuthError() from None
        raise
    return raw_token


async def resolve_session_user(db: AsyncSession, raw_token: str | None) -> User | None:
    if not valid_token(raw_token):
        return None
    return await db.scalar(
        select(User)
        .join(AuthSession, AuthSession.steamid64 == User.steamid64)
        .where(
            AuthSession.token_hash == token_hash(raw_token),
            AuthSession.expires_at > datetime.now(UTC),
        )
    )


async def logout_session(db: AsyncSession, raw_token: str | None) -> None:
    if not valid_token(raw_token):
        return
    async with db.begin():
        await db.execute(
            delete(AuthSession).where(AuthSession.token_hash == token_hash(raw_token))
        )

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.api.auth import get_current_user
from gamerec.core.auth_security import require_account_owner, require_trusted_origin
from gamerec.db import get_db
from gamerec.models.user import User
from gamerec.schemas.user_game_preference import (
    UserGamePreferenceRequest,
    UserGamePreferenceResponse,
)
from gamerec.schemas.user_library import LibrarySyncResponse, UserLibraryResponse
from gamerec.services.manual_library_sync import (
    LibrarySyncCoordinator,
    LibrarySyncError,
    sync_library,
)
from gamerec.services.user_game_preferences import (
    GameNotFoundError,
    GameNotOwnedError,
    UserNotFoundError,
    delete_user_game_preference,
    get_user_game_preference,
    upsert_user_game_preference,
)
from gamerec.services.user_library import get_user_library

router = APIRouter(
    prefix="/users",
    tags=["users"],
)


async def get_sync_coordinator(request: Request) -> LibrarySyncCoordinator:
    return request.app.state.library_sync_coordinator


def preference_not_found_error(exc: Exception) -> HTTPException:
    if isinstance(exc, UserNotFoundError):
        code, message = "user_not_found", "Stored user not found."
    elif isinstance(exc, GameNotOwnedError):
        code, message = "game_not_owned", "Game is not in this stored library."
    else:
        code, message = "game_not_found", "Game not found."
    return HTTPException(404, detail={"code": code, "message": message})


def preference_storage_error(exc: Exception) -> HTTPException:
    logging.getLogger(__name__).warning(
        "Preference storage failed (%s)", type(exc).__name__
    )
    return HTTPException(
        503,
        detail={
            "code": "database_unavailable",
            "message": "Preference storage is temporarily unavailable.",
        },
    )


def account_storage_error(exc: Exception) -> HTTPException:
    logging.getLogger(__name__).warning(
        "Account storage failed (%s)", type(exc).__name__
    )
    return HTTPException(
        503,
        detail={
            "code": "database_unavailable",
            "message": "Account storage is temporarily unavailable.",
        },
    )


@router.post("/{steamid64}/library/sync", response_model=LibrarySyncResponse)
async def sync_user_library_endpoint(
    request: Request,
    steamid64: Annotated[str, Path(pattern=r"^[0-9]{17}$")],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    coordinator: Annotated[LibrarySyncCoordinator, Depends(get_sync_coordinator)],
) -> LibrarySyncResponse:
    require_account_owner(current_user.steamid64, steamid64)
    require_trusted_origin(request)
    try:
        return await sync_library(db, current_user.steamid64, coordinator)
    except LibrarySyncError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
            headers={"Retry-After": str(exc.retry_after)} if exc.retry_after else None,
        ) from None
    except (TimeoutError, httpx.TimeoutException):
        raise HTTPException(
            504,
            detail={
                "code": "sync_timeout",
                "message": "Sync timed out. Refresh stored data before retrying.",
            },
        ) from None
    except (SQLAlchemyError, OSError) as exc:
        logging.getLogger(__name__).warning(
            "Library sync dependency failed (%s)", type(exc).__name__
        )
        raise HTTPException(
            503,
            detail={
                "code": "database_unavailable",
                "message": "Storage is unavailable. Refresh stored data before retrying.",
            },
        ) from None
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(
            502,
            detail={
                "code": "steam_upstream_error",
                "message": "Steam could not provide usable library data. Refresh stored data before retrying.",
            },
        ) from None


@router.get(
    "/{steamid64}/library",
    response_model=UserLibraryResponse,
    summary="Get a user's game library",
)
async def get_user_library_endpoint(
    db: Annotated[AsyncSession, Depends(get_db)],
    steamid64: Annotated[str, Path(pattern=r"^[0-9]{17}$")],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(gt=0, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserLibraryResponse:
    require_account_owner(current_user.steamid64, steamid64)
    try:
        user_game_library = await get_user_library(
            db=db,
            steamid64=current_user.steamid64,
            limit=limit,
            offset=offset,
        )
    except (SQLAlchemyError, OSError) as exc:
        raise account_storage_error(exc) from None

    if user_game_library is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "user_not_found", "message": "Stored user not found."},
        )

    user_game_library["sync_allowed"] = True
    user_game_library["preference_writes_allowed"] = True
    return user_game_library


@router.put(
    "/{steamid64}/preferences/{steam_app_id}",
    summary="Update a user's game preference",
    response_model=UserGamePreferenceResponse,
)
async def update_user_game_preference(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    steamid64: Annotated[str, Path(pattern=r"^[0-9]{17}$")],
    steam_app_id: Annotated[int, Path(gt=0, le=2**31 - 1)],
    preference_request: UserGamePreferenceRequest,
) -> UserGamePreferenceResponse:
    require_account_owner(current_user.steamid64, steamid64)
    require_trusted_origin(request)
    try:
        async with db.begin():
            saved_preference = await upsert_user_game_preference(
                db=db,
                steamid64=current_user.steamid64,
                steam_app_id=steam_app_id,
                preference=preference_request.preference.value,
            )

    except (UserNotFoundError, GameNotFoundError, GameNotOwnedError) as exc:
        raise preference_not_found_error(exc) from None
    except (SQLAlchemyError, OSError) as exc:
        raise preference_storage_error(exc) from None

    response = UserGamePreferenceResponse.model_validate(saved_preference)

    return response


@router.get(
    "/{steamid64}/preferences",
    summary="Get all game preferences from a user",
    response_model=list[UserGamePreferenceResponse],
)
async def list_user_game_preferences(
    db: Annotated[AsyncSession, Depends(get_db)],
    steamid64: Annotated[str, Path(pattern=r"^[0-9]{17}$")],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(gt=0, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[UserGamePreferenceResponse]:
    require_account_owner(current_user.steamid64, steamid64)
    try:
        async with db.begin():
            user_game_preferences = await get_user_game_preference(
                db=db,
                steamid64=current_user.steamid64,
                limit=limit,
                offset=offset,
            )

    except UserNotFoundError as exc:
        raise preference_not_found_error(exc) from None
    except (SQLAlchemyError, OSError) as exc:
        raise account_storage_error(exc) from None

    return user_game_preferences


@router.delete(
    "/{steamid64}/preferences/{steam_app_id}",
    status_code=204,
    summary="Delete a game preference from a user",
)
async def remove_user_game_preference(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    steamid64: Annotated[str, Path(pattern=r"^[0-9]{17}$")],
    steam_app_id: Annotated[int, Path(gt=0, le=2**31 - 1)],
) -> None:
    require_account_owner(current_user.steamid64, steamid64)
    require_trusted_origin(request)
    try:
        async with db.begin():
            await delete_user_game_preference(
                db=db,
                steamid64=current_user.steamid64,
                steam_app_id=steam_app_id,
            )

    except (UserNotFoundError, GameNotFoundError) as exc:
        raise preference_not_found_error(exc) from None
    except (SQLAlchemyError, OSError) as exc:
        raise preference_storage_error(exc) from None

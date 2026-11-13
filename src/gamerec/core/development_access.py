"""Local test-account restrictions; these do not authenticate an account owner."""

import re

from gamerec.core.config import settings


def is_development_test_account(steamid64: str) -> bool:
    allowed = settings.steamid64_test
    return (
        settings.environment == "development"
        and isinstance(allowed, str)
        and re.fullmatch(r"[0-9]{17}", allowed) is not None
        and steamid64 == allowed
    )


def preference_writes_allowed(steamid64: str) -> bool:
    return settings.user_preference_writes_enabled and is_development_test_account(
        steamid64
    )

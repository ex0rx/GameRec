"""Account response privacy and redaction of assertion/identity URLs."""

import logging
import re

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class AuthLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if (
            record.name == "uvicorn.access"
            and isinstance(record.args, tuple)
            and len(record.args) == 5
        ):
            args = list(record.args)
            if (
                str(args[2])
                .split("?", 1)[0]
                .rstrip("/")
                .endswith("/auth/steam/callback")
            ):
                args[2] = str(args[2]).split("?", 1)[0] + "?[redacted]"
                record.args = tuple(args)
            return True
        original = record.getMessage()
        message = re.sub(r"(/openid/id/)[0-9]+", r"\1[redacted]", original)
        message = re.sub(
            r"(/auth/steam/callback/?)\?[^\s\"']*", r"\1?[redacted]", message
        )
        if message != original:
            record.msg, record.args = message, ()
        return True


def private_account_path(path: str) -> bool:
    return (
        path.startswith(("/auth/", "/users/")) or path.rstrip("/") == "/recommendations"
    )


def install_auth_log_filters() -> None:
    for name in (
        "uvicorn.access",
        "httpx",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.connection",
        "httpcore.proxy",
    ):
        logger = logging.getLogger(name)
        if not any(isinstance(item, AuthLogFilter) for item in logger.filters):
            logger.addFilter(AuthLogFilter())


class AuthPrivacyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not private_account_path(path):
            await self.app(scope, receive, send)
            return

        async def private_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        if (
            scope["path"].rstrip("/") == "/auth/steam/callback"
            and scope["path"] != "/auth/steam/callback"
        ):
            # Do not let slash normalisation redirect an assertion using the
            # proxy's internal Host or forward its query to another destination.
            response = JSONResponse(
                status_code=400,
                content={
                    "detail": {
                        "code": "authentication_failed",
                        "message": "Invalid callback destination.",
                    }
                },
            )
            await response(scope, receive, private_send)
            return
        await self.app(scope, receive, private_send)

from __future__ import annotations

import hmac
import re
import time
import uuid
from collections import OrderedDict, deque
from http.cookies import SimpleCookie

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import Settings

log = structlog.get_logger()
SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")


class RequestGuards:
    """Bound request memory, enforce same-origin cookie writes, and record safe HTTP metadata."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.windows: OrderedDict[str, deque[float]] = OrderedDict()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        candidate = headers.get("x-request-id", "")
        request_id = candidate if SAFE_ID.fullmatch(candidate) else str(uuid.uuid4())
        correlation = headers.get("x-correlation-id", request_id)
        if not SAFE_ID.fullmatch(correlation):
            correlation = request_id
        scope.setdefault("state", {}).update(request_id=request_id, correlation_id=correlation)
        start = time.monotonic()

        async def reject(code: int, error: str, message: str) -> None:
            await JSONResponse(
                {"error": message, "code": error, "request_id": request_id},
                status_code=code,
                headers={"X-Request-ID": request_id},
            )(scope, receive, send)

        client = scope.get("client")
        ip = str(client[0]) if client else "unknown"
        window = self.windows.setdefault(ip, deque())
        self.windows.move_to_end(ip)
        while len(self.windows) > 20000:
            self.windows.popitem(last=False)
        while window and window[0] <= start - 60:
            window.popleft()
        notification_service = str(scope.get("path", "")).startswith(
            "/api/v1/internal/notifications/"
        ) and hmac.compare_digest(
            headers.get("x-horizon-bot-token", ""), self.settings.discord_bot_internal_token.get_secret_value()
        )
        atm_token = self.settings.atm_service_token.get_secret_value()
        atm_service = (
            bool(atm_token)
            and str(scope.get("path", "")).startswith("/api/v1/internal/atm/")
            and hmac.compare_digest(headers.get("authorization", "").encode(), ("Bearer " + atm_token).encode())
        )
        if (
            not (notification_service or atm_service)
            and scope.get("path") not in {"/healthz", "/readyz"}
            and len(window) >= self.settings.rate_limit_per_minute
        ):
            await reject(429, "rate_limited", "too many requests")
            return
        if not (notification_service or atm_service):
            window.append(start)
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except Exception:
            await reject(400, "invalid_cookie", "invalid cookie header")
            return
        if (
            scope["method"] not in {"GET", "HEAD", "OPTIONS"}
            and ("horizon_session" in cookie or scope.get("path") in {"/api/v1/auth/register", "/api/v1/auth/login"})
            and (
                scope.get("path") in {"/api/v1/auth/register", "/api/v1/auth/login"}
                or not headers.get("authorization", "").startswith("Bearer ")
            )
        ):
            if headers.get("origin") != str(self.settings.public_origin).rstrip("/"):
                await reject(403, "csrf_rejected", "same-origin request required")
                return
        length = headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdigit():
                await reject(400, "invalid_content_length", "invalid Content-Length")
                return
            if len(length) > 20 or int(length) > self.settings.max_request_bytes:
                await reject(413, "request_too_large", "request body exceeds limit")
                return
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.settings.max_request_bytes:
                await reject(413, "request_too_large", "request body exceeds limit")
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        response_status = 500

        async def tagged_send(message: Message) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        try:
            await self.app(scope, replay, tagged_send)
        finally:
            log.info(
                "http_request",
                service="api",
                request_id=request_id,
                correlation_id=correlation,
                method=scope["method"],
                status=response_status,
                duration_ms=round((time.monotonic() - start) * 1000),
            )

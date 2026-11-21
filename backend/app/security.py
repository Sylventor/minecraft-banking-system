from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .database import get_session
from .models import ApiKey, ExternalIdentity, User, UserSession
from .rbac import permissions

KEY_PATTERN = re.compile(r"^hbk_([a-zA-Z0-9]{12})_([a-zA-Z0-9_-]{32,128})$")
VALID_SCOPES = frozenset(
    {
        "profile.read",
        "accounts.read",
        "accounts.create",
        "balance.read",
        "history.read",
        "transfers.create",
        "payments.create",
        "holds.create",
        "organizations.manage",
        "recipients.read",
        "payment_requests.create",
        "payment_requests.read",
        "payment_requests.cancel",
        "analytics.read",
        "analytics.export",
        "exchange.read",
        "exchange.trade",
    }
)


@dataclass(frozen=True)
class Principal:
    user_id: str
    scopes: frozenset[str]
    via_api_key: bool = False
    account_ids: frozenset[str] | None = None
    via_bot: bool = False
    organization_id: str | None = None
    api_key_id: str | None = None


def secret_hash(secret: str, settings: Settings) -> str:
    return hmac.new(settings.api_key_pepper.get_secret_value().encode(), secret.encode(), hashlib.sha256).hexdigest()


def new_api_key(settings: Settings) -> tuple[str, str, str]:
    prefix = secrets.token_urlsafe(9).replace("-", "A").replace("_", "B")[:12]
    secret = secrets.token_urlsafe(36)
    return prefix, secret, f"hbk_{prefix}_{secret}"


async def current_principal(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Principal:
    bot_token = request.headers.get("x-horizon-bot-token")
    discord_id = request.headers.get("x-discord-user-id")
    if bot_token or discord_id:
        if not (
            bot_token
            and discord_id
            and hmac.compare_digest(bot_token, settings.discord_bot_internal_token.get_secret_value())
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid bot authentication")
        identity = await session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.provider == "discord", ExternalIdentity.external_id == discord_id
            )
        )
        if identity is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Discord account is not linked")
        owner = await session.get(User, identity.user_id)
        if owner is None or owner.status != "active" or owner.processing_restricted_at is not None or owner.erased_at is not None:
            raise HTTPException(401, "profile is not active")
        return Principal(str(identity.user_id), frozenset(VALID_SCOPES), via_bot=True)
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        match = KEY_PATTERN.fullmatch(authorization.removeprefix("Bearer "))
        if not match:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")
        prefix, secret = match.groups()
        key = await session.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
        if (
            key is None
            or key.revoked_at is not None
            or (key.expires_at is not None and key.expires_at <= datetime.now(UTC))
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key is invalid or revoked")
        if not hmac.compare_digest(key.secret_hash, secret_hash(secret, settings)):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key is invalid or revoked")
        owner = await session.get(User, key.owner_id)
        if owner is None or owner.status != "active" or owner.processing_restricted_at is not None or owner.erased_at is not None:
            raise HTTPException(401, "profile is not active")
        if key.organization_id is not None and not (
            request.url.path.startswith("/api/v1/payment-requests") or request.url.path == "/api/v1/recipients/search"
        ):
            raise HTTPException(403, "Merchant keys can only manage payment requests")
        return Principal(
            str(key.owner_id),
            frozenset(key.scopes),
            True,
            frozenset(str(item) for item in key.account_ids),
            organization_id=str(key.organization_id) if key.organization_id else None,
            api_key_id=str(key.id),
        )
    cookie = request.cookies.get("horizon_session", "")
    if not cookie or len(cookie) > 128:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    record = await session.scalar(
        select(UserSession)
        .where(UserSession.token_hash == hashlib.sha256(cookie.encode()).hexdigest())
        .with_for_update()
    )
    moment = datetime.now(UTC)
    if (
        record is None
        or record.revoked_at is not None
        or record.expires_at <= moment
        or record.absolute_expires_at <= moment
    ):
        raise HTTPException(401, "session expired or revoked")
    user = await session.get(User, record.user_id)
    if user is None or user.status != "active" or user.processing_restricted_at is not None or user.erased_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session is not active")
    request.state.authenticated_at = record.created_at
    request.state.auth_method = record.auth_method
    if (moment - record.last_activity_at).total_seconds() >= 300:
        record.last_activity_at = moment
        record.expires_at = min(moment + timedelta(seconds=settings.session_ttl_seconds), record.absolute_expires_at)
        response.set_cookie(
            "horizon_session",
            cookie,
            httponly=True,
            secure=settings.production,
            samesite="lax",
            path="/",
            max_age=max(1, int((record.expires_at - moment).total_seconds())),
        )
    await session.commit()
    return Principal(str(user.id), frozenset(VALID_SCOPES), False)


def require_scope(scope: str) -> Callable[..., Awaitable[Principal]]:
    async def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.via_api_key and scope not in principal.scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "API key scope is not granted")
        return principal

    return dependency


def require_resource(principal: Principal, account_id: str) -> None:
    if principal.via_api_key and (principal.account_ids is None or account_id not in principal.account_ids):
        raise HTTPException(403, "API key does not grant access to this account")


def require_admin(permission: str) -> Callable[..., Awaitable[Principal]]:
    async def dependency(
        request: Request,
        principal: Principal = Depends(current_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Principal:
        if principal.via_api_key or principal.via_bot:
            raise HTTPException(403, "administrative access requires an interactive session")
        if permission not in await permissions(session, principal.user_id):
            raise HTTPException(403, "administrative permission is not granted")
        if (
            request.method not in {"GET", "HEAD"}
            and (datetime.now(UTC) - request.state.authenticated_at).total_seconds() > 900
        ):
            raise HTTPException(403, "Повторно подтвердите вход для чувствительной операции")
        return principal

    return dependency

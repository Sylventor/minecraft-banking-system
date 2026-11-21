"""Opaque, revocable PostgreSQL sessions; refresh rotates tokens, never an access JWT."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..database import get_session
from ..experience import audit
from ..models import UserSession
from ..security import Principal, current_principal

router = APIRouter(prefix="/api/v1", tags=["session security"])


@router.post("/me/sessions/revoke-others", status_code=204)
async def revoke_others(
    request: Request, principal: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)
) -> Response:
    if principal.via_api_key or principal.via_bot:
        raise HTTPException(403, "Interactive session required")
    current = hashlib.sha256(request.cookies["horizon_session"].encode()).hexdigest()
    await session.execute(
        update(UserSession)
        .where(
            UserSession.user_id == uuid.UUID(principal.user_id),
            UserSession.token_hash != current,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    audit(session, principal.user_id, "security.sessions.revoke_others", principal.user_id)
    await session.commit()
    return Response(status_code=204)


@router.post("/auth/session/refresh", status_code=204)
async def refresh(
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    if principal.via_api_key or principal.via_bot:
        raise HTTPException(403, "Interactive session required")
    key = hashlib.sha256(request.cookies["horizon_session"].encode()).hexdigest()
    old = await session.scalar(select(UserSession).where(UserSession.token_hash == key).with_for_update())
    moment = datetime.now(UTC)
    if old is None or old.revoked_at is not None or min(old.expires_at, old.absolute_expires_at) <= moment:
        raise HTTPException(401, "Session revoked")
    old.revoked_at = moment
    token = secrets.token_urlsafe(48)
    expiry = min(moment + timedelta(seconds=settings.session_ttl_seconds), old.absolute_expires_at)
    session.add(
        UserSession(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            user_id=old.user_id,
            created_at=old.created_at,
            auth_method=old.auth_method,
            expires_at=expiry,
            absolute_expires_at=old.absolute_expires_at,
            last_activity_at=moment,
            client_info=request.headers.get("user-agent", "")[:180],
        )
    )
    audit(session, principal.user_id, "security.session.rotate", str(old.public_id))
    await session.commit()
    response = Response(status_code=204)
    response.set_cookie(
        "horizon_session",
        token,
        max_age=max(1, int((expiry - moment).total_seconds())),
        httponly=True,
        secure=settings.production,
        samesite="lax",
        path="/",
    )
    return response

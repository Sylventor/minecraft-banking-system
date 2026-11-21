from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..database import get_session
from ..models import AuditEvent, MinecraftChallenge, MinecraftVerifiedLink, User
from ..schemas import StrictModel
from ..security import Principal, current_principal, require_scope

router = APIRouter(prefix="/api/v1/minecraft", tags=["minecraft"])


class MinecraftCodeResponse(BaseModel):
    challenge_id: uuid.UUID
    code: str
    expires_at: datetime
    note: str


class MinecraftIdentity(BaseModel):
    player_uuid: uuid.UUID
    nickname: str
    verified_at: datetime


class MinecraftProfile(BaseModel):
    integration_enabled: bool
    link: MinecraftIdentity | None


class MinecraftAttestation(StrictModel):
    challenge_id: uuid.UUID
    code: Annotated[str, Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
    player_uuid: uuid.UUID
    nickname: Annotated[str, Field(pattern=r"^[A-Za-z0-9_]{3,16}$")]
    authentication: Literal["online-mode"]


async def lock_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    # Both legacy server attestations and website/ATM challenges share the same
    # identity namespace. Serialize mutations before taking per-user locks.
    await session.execute(text("SELECT pg_advisory_xact_lock(220027)"))
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": "minecraft-user:" + str(user_id)},
    )


def evidence(session: AsyncSession, user_id: uuid.UUID, action: str, metadata: dict[str, object]) -> None:
    session.add(
        AuditEvent(
            actor_id=user_id,
            action="minecraft." + action,
            target_type="user",
            target_id=str(user_id),
            metadata_json=metadata,
        )
    )


@router.get("", response_model=MinecraftProfile)
async def profile(
    principal: Principal = Depends(require_scope("profile.read")),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MinecraftProfile:
    link = await session.get(MinecraftVerifiedLink, uuid.UUID(principal.user_id))
    return MinecraftProfile(
        integration_enabled=bool(settings.minecraft_verifier_token.get_secret_value()),
        link=MinecraftIdentity(player_uuid=link.player_uuid, nickname=link.nickname, verified_at=link.verified_at)
        if link
        else None,
    )


@router.post("/link-code", response_model=MinecraftCodeResponse, status_code=201)
async def create_code(
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MinecraftCodeResponse:
    if principal.via_api_key:
        raise HTTPException(403, "linking requires an interactive session or the trusted Discord bot")
    if not settings.minecraft_verifier_token.get_secret_value():
        raise HTTPException(503, "Minecraft verification integration is not configured")
    user_id = uuid.UUID(principal.user_id)
    await lock_user(session, user_id)
    now = datetime.now(UTC)
    previous = await session.scalar(
        select(MinecraftChallenge)
        .where(MinecraftChallenge.user_id == user_id)
        .order_by(MinecraftChallenge.created_at.desc())
        .limit(1)
    )
    if previous and previous.created_at > now - timedelta(seconds=60):
        raise HTTPException(429, "wait one minute before issuing another link code")
    await session.execute(
        update(MinecraftChallenge)
        .where(MinecraftChallenge.user_id == user_id, MinecraftChallenge.consumed_at.is_(None))
        .values(revoked_at=now)
    )
    code = secrets.token_urlsafe(24)
    challenge = MinecraftChallenge(
        id=uuid.uuid4(),
        user_id=user_id,
        code_hash=hashlib.sha256(code.encode()).hexdigest(),
        expires_at=now + timedelta(minutes=10),
    )
    session.add(challenge)
    evidence(session, user_id, "challenge.create", {"challenge_id": str(challenge.id)})
    await session.commit()
    return MinecraftCodeResponse(
        challenge_id=challenge.id,
        code=code,
        expires_at=challenge.expires_at,
        note="Use only on the trusted online-mode Minecraft server. A code alone does not prove ownership.",
    )


@router.post("/verify", response_model=MinecraftIdentity)
async def verify(
    body: MinecraftAttestation,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MinecraftIdentity:
    expected = settings.minecraft_verifier_token.get_secret_value()
    if not expected:
        raise HTTPException(503, "Minecraft verification integration is not configured")
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied.encode(), ("Bearer " + expected).encode()):
        raise HTTPException(401, "trusted Minecraft verifier authentication required")
    challenge = await session.get(MinecraftChallenge, body.challenge_id)
    if challenge is None:
        raise HTTPException(404, "link challenge is unavailable")
    # All link mutations lock user first, then challenge, then player to avoid inversion.
    await lock_user(session, challenge.user_id)
    challenge = await session.scalar(
        select(MinecraftChallenge)
        .where(MinecraftChallenge.id == body.challenge_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if challenge is None:
        raise HTTPException(404, "link challenge is unavailable")
    now = datetime.now(UTC)
    if challenge.consumed_at or challenge.revoked_at:
        raise HTTPException(409, "link challenge was already used or revoked")
    if challenge.expires_at <= now:
        raise HTTPException(410, "link challenge expired")
    if challenge.attempts >= 5:
        raise HTTPException(429, "link challenge attempt limit reached")
    if not hmac.compare_digest(challenge.code_hash, hashlib.sha256(body.code.encode()).hexdigest()):
        challenge.attempts += 1
        if challenge.attempts == 5:
            evidence(session, challenge.user_id, "challenge.locked", {"challenge_id": str(challenge.id)})
        await session.commit()  # Failed attempts must survive the rejected request.
        raise HTTPException(401, "invalid link code")
    owner = await session.get(User, challenge.user_id)
    if owner is None or owner.status != "active":
        raise HTTPException(403, "profile is not active")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": "minecraft-player:" + str(body.player_uuid)},
    )
    claimed = await session.scalar(
        select(MinecraftVerifiedLink).where(MinecraftVerifiedLink.player_uuid == body.player_uuid)
    )
    if claimed and claimed.user_id != challenge.user_id:
        raise HTTPException(409, "Minecraft identity is already linked")
    link = await session.get(MinecraftVerifiedLink, challenge.user_id)
    if link and link.player_uuid != body.player_uuid:
        raise HTTPException(409, "unlink the existing Minecraft identity before linking another")
    if link is None:
        link = MinecraftVerifiedLink(user_id=challenge.user_id, player_uuid=body.player_uuid)
        session.add(link)
    link.nickname = body.nickname
    link.authentication = "online-mode"
    link.verified_at = now
    await session.execute(
        text(
            "INSERT INTO auth.minecraft_nickname_history(id,user_id,player_uuid,nickname,verified_at) VALUES(:id,:user,:player,:nickname,:verified)"
        ),
        {
            "id": uuid.uuid4(),
            "user": challenge.user_id,
            "player": body.player_uuid,
            "nickname": body.nickname,
            "verified": now,
        },
    )
    challenge.consumed_at = now
    evidence(session, challenge.user_id, "link.verified", {"player_uuid": str(body.player_uuid)})
    await session.commit()
    return MinecraftIdentity(player_uuid=link.player_uuid, nickname=link.nickname, verified_at=link.verified_at)


@router.delete("", status_code=204)
async def unlink(
    principal: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)
) -> Response:
    if principal.via_api_key or principal.via_bot:
        raise HTTPException(403, "unlinking requires an interactive session")
    user_id = uuid.UUID(principal.user_id)
    await lock_user(session, user_id)
    link = await session.get(MinecraftVerifiedLink, user_id)
    if link:
        evidence(session, user_id, "link.remove", {})
        await session.delete(link)
    await session.flush()
    for table in ("minecraft_links", "minecraft_link_challenges", "minecraft_atm_challenges", "minecraft_nickname_history"):
        await session.execute(text(f"DELETE FROM auth.{table} WHERE user_id=:u"), {"u": user_id})
    await session.execute(text("UPDATE auth.audit_events SET metadata_json='{}' WHERE actor_id=:u AND action LIKE 'minecraft.link.%'"), {"u":user_id})
    await session.commit()
    return Response(status_code=204)

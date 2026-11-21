"""Website confirmation of a code delivered to a live, server-authenticated player."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..atm_delivery import inspect_delivery
from ..config import Settings, get_settings
from ..database import get_session
from ..schemas import StrictModel
from ..security import Principal, current_principal
from .minecraft import evidence, lock_user

router = APIRouter(prefix="/api/v1/minecraft/link", tags=["Minecraft website linking"])
log = structlog.get_logger(__name__)
DELIVERY_ERROR = (
    "Не удалось отправить код подтверждения. Убедитесь, что вы находитесь на Minecraft-сервере и правильно указали ник."
)


class Start(StrictModel):
    nickname: str = Field(pattern=r"^[A-Za-z0-9_]{3,16}$")


class Verify(StrictModel):
    challenge_id: uuid.UUID
    code: str = Field(pattern=r"^[0-9]{6}$")


def interactive(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.via_api_key or principal.via_bot:
        raise HTTPException(403, "Привязка Minecraft доступна только в личном кабинете")
    return principal


def digest(settings: Settings, user: uuid.UUID, challenge: uuid.UUID, nickname: str, code: str) -> str:
    value = f"minecraft-atm:{user}:{challenge}:{nickname.lower()}:{code}"
    return hmac.new(settings.api_key_pepper.get_secret_value().encode(), value.encode(), hashlib.sha256).hexdigest()


async def expire(session: AsyncSession) -> None:
    await session.execute(
        text(
            "UPDATE auth.minecraft_atm_challenges SET status='expired' "
            "WHERE status IN ('pending','delivered') AND expires_at<=now()"
        )
    )


@router.get("/status")
async def status(
    principal: Principal = Depends(interactive),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    user = uuid.UUID(principal.user_id)
    await lock_user(session, user)
    await expire(session)
    link = (
        (
            await session.execute(
                text(
                    "SELECT nickname,player_uuid,verified_at,authentication "
                    "FROM auth.minecraft_verified_links WHERE user_id=:u"
                ),
                {"u": user},
            )
        )
        .mappings()
        .first()
    )
    pending = (
        (
            await session.execute(
                text(
                    "SELECT id,nickname,status,expires_at,attempts FROM auth.minecraft_atm_challenges "
                    "WHERE user_id=:u AND status IN ('pending','delivered')"
                ),
                {"u": user},
            )
        )
        .mappings()
        .first()
    )
    await session.commit()
    return {
        "integration_enabled": bool(settings.atm_service_token.get_secret_value()),
        "link": dict(link) if link else None,
        "pending": dict(pending) if pending else None,
    }


@router.post("/start", status_code=201)
async def start(
    body: Start,
    principal: Principal = Depends(interactive),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    token = settings.atm_service_token.get_secret_value()
    if not token:
        raise HTTPException(503, "Система Minecraft-банкоматов пока не настроена")
    user = uuid.UUID(principal.user_id)
    await lock_user(session, user)
    await expire(session)
    if (
        await session.execute(
            text("SELECT 1 FROM auth.minecraft_verified_links WHERE lower(nickname)=lower(:n) AND user_id<>:u"),
            {"n": body.nickname, "u": user},
        )
    ).first():
        raise HTTPException(409, "Этот Minecraft-аккаунт уже привязан к другому пользователю")
    recent = (
        await session.execute(
            text(
                "SELECT count(*) FILTER(WHERE created_at>now()-interval '60 seconds'),count(*) "
                "FROM auth.minecraft_atm_challenges WHERE (user_id=:u OR lower(nickname)=lower(:n)) AND created_at>now()-interval '1 hour'"
            ),
            {"u": user, "n": body.nickname},
        )
    ).one()
    if recent[0] or recent[1] >= 10:
        evidence(session, user, "atm.rate_limited", {})
        await session.commit()
        raise HTTPException(429, "Подождите минуту перед повторным запросом. Не более 10 кодов в час.")
    if (
        await session.execute(
            text(
                "SELECT 1 FROM auth.minecraft_atm_challenges WHERE lower(nickname)=lower(:n) "
                "AND user_id<>:u AND status IN ('pending','delivered')"
            ),
            {"u": user, "n": body.nickname},
        )
    ).first():
        raise HTTPException(409, "Для этого Minecraft-ника уже ожидается подтверждение")
    await session.execute(
        text(
            "UPDATE auth.minecraft_atm_challenges SET status='cancelled' WHERE user_id=:u AND status IN ('pending','delivered')"
        ),
        {"u": user},
    )
    challenge, code = uuid.uuid4(), f"{secrets.randbelow(1000000):06d}"
    expires = datetime.now(UTC) + timedelta(minutes=5)
    await session.execute(
        text(
            "INSERT INTO auth.minecraft_atm_challenges(id,user_id,nickname,code_hash,status,expires_at) "
            "VALUES(:id,:u,:n,:hash,'pending',:expires)"
        ),
        {
            "id": challenge,
            "u": user,
            "n": body.nickname,
            "hash": digest(settings, user, challenge, body.nickname, code),
            "expires": expires,
        },
    )
    evidence(session, user, "atm.challenge.create", {"challenge_id": str(challenge), "nickname": body.nickname})
    await session.commit()  # Never retain identity locks while waiting for Minecraft.
    delivered: dict[str, Any] | None = None
    failure_reason = "coordinator_unreachable"
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.post(
                settings.atm_coordinator_url.rstrip("/") + "/deliver",
                headers={"Authorization": "Bearer " + token},
                json={"challenge_id": str(challenge), "nickname": body.nickname, "code": code},
            )
        delivered, failure_reason = inspect_delivery(response, settings.atm_identity_mode)
    except httpx.TimeoutException:
        failure_reason = "coordinator_timeout"
    except httpx.HTTPError:
        failure_reason = "coordinator_unreachable"
    except (ValueError, KeyError, TypeError):
        failure_reason = "invalid_response"
    await lock_user(session, user)
    row = (
        await session.execute(
            text("SELECT status FROM auth.minecraft_atm_challenges WHERE id=:id FOR UPDATE"), {"id": challenge}
        )
    ).one()
    if row[0] != "pending":
        raise HTTPException(409, "Заявка отменена. Запросите новый код.")
    if delivered is None:
        await session.execute(
            text("UPDATE auth.minecraft_atm_challenges SET status='failed' WHERE id=:id"), {"id": challenge}
        )
        evidence(session, user, "atm.delivery.failed", {"challenge_id": str(challenge), "reason": failure_reason})
        await session.commit()
        log.warning(
            "minecraft_atm.delivery.failed",
            challenge_id=str(challenge),
            reason=failure_reason,
            expected_mode=settings.atm_identity_mode,
        )
        raise HTTPException(503, DELIVERY_ERROR)
    await session.execute(
        text(
            "UPDATE auth.minecraft_atm_challenges SET status='delivered',player_uuid=:p,authentication=:a,delivered_at=now() WHERE id=:id"
        ),
        {"id": challenge, "p": uuid.UUID(delivered["player_uuid"]), "a": delivered["authentication"]},
    )
    evidence(session, user, "atm.delivery.confirmed", {"challenge_id": str(challenge)})
    await session.commit()
    log.info("minecraft_atm.delivery.confirmed", challenge_id=str(challenge), authentication=settings.atm_identity_mode)
    return {"challenge_id": challenge, "nickname": body.nickname, "expires_at": expires, "status": "delivered"}


@router.post("/verify")
async def verify(
    body: Verify,
    principal: Principal = Depends(interactive),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    user = uuid.UUID(principal.user_id)
    await lock_user(session, user)
    row = (
        (
            await session.execute(
                text("SELECT * FROM auth.minecraft_atm_challenges WHERE id=:id AND user_id=:u FOR UPDATE"),
                {"id": body.challenge_id, "u": user},
            )
        )
        .mappings()
        .first()
    )
    if not row or row["status"] != "delivered" or row["expires_at"] <= datetime.now(UTC):
        evidence(session, user, "atm.verify.unavailable", {"challenge_id": str(body.challenge_id)})
        await session.commit()
        raise HTTPException(409, "Код недоступен, уже использован или истёк. Запросите новый код.")
    if not hmac.compare_digest(row["code_hash"], digest(settings, user, body.challenge_id, row["nickname"], body.code)):
        await session.execute(
            text(
                "UPDATE auth.minecraft_atm_challenges SET attempts=attempts+1,"
                "status=CASE WHEN attempts>=4 THEN 'locked' ELSE status END WHERE id=:id"
            ),
            {"id": body.challenge_id},
        )
        evidence(
            session, user, "atm.verify.failed", {"challenge_id": str(body.challenge_id), "attempt": row["attempts"] + 1}
        )
        await session.commit()
        raise HTTPException(
            401, "Неверный код подтверждения" if row["attempts"] < 4 else "Лимит попыток исчерпан. Запросите новый код."
        )
    claimed = (
        await session.execute(
            text(
                "SELECT user_id FROM auth.minecraft_verified_links "
                "WHERE (player_uuid=:p OR lower(nickname)=lower(:n)) AND user_id<>:u"
            ),
            {"p": row["player_uuid"], "n": row["nickname"], "u": user},
        )
    ).first()
    if claimed:
        raise HTTPException(409, "Этот Minecraft-аккаунт уже привязан к другому пользователю")
    await session.execute(
        text(
            "INSERT INTO auth.minecraft_verified_links(user_id,player_uuid,nickname,verified_at,authentication) "
            "VALUES(:u,:p,:n,now(),:a) ON CONFLICT(user_id) DO UPDATE SET player_uuid=EXCLUDED.player_uuid,nickname=EXCLUDED.nickname,"
            "verified_at=now(),authentication=EXCLUDED.authentication"
        ),
        {"u": user, "p": row["player_uuid"], "n": row["nickname"], "a": row["authentication"]},
    )
    await session.execute(
        text(
            "INSERT INTO auth.minecraft_nickname_history(id,user_id,player_uuid,nickname,verified_at) VALUES(:id,:u,:p,:n,now())"
        ),
        {"id": uuid.uuid4(), "u": user, "p": row["player_uuid"], "n": row["nickname"]},
    )
    await session.execute(
        text("UPDATE auth.minecraft_atm_challenges SET status='consumed',consumed_at=now() WHERE id=:id"),
        {"id": body.challenge_id},
    )
    await session.execute(
        text("UPDATE auth.minecraft_link_challenges SET revoked_at=now() WHERE user_id=:u AND consumed_at IS NULL"),
        {"u": user},
    )
    evidence(
        session,
        user,
        "atm.link.verified",
        {"player_uuid": str(row["player_uuid"]), "authentication": row["authentication"]},
    )
    await session.commit()
    return {"nickname": row["nickname"], "verified": True}


@router.post("/cancel", status_code=204)
async def cancel(principal: Principal = Depends(interactive), session: AsyncSession = Depends(get_session)) -> None:
    user = uuid.UUID(principal.user_id)
    await lock_user(session, user)
    await session.execute(
        text(
            "UPDATE auth.minecraft_atm_challenges SET status='cancelled' WHERE user_id=:u AND status IN ('pending','delivered')"
        ),
        {"u": user},
    )
    evidence(session, user, "atm.challenge.cancel", {})
    await session.commit()

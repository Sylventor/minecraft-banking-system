from __future__ import annotations

import hmac
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..database import get_session
from ..experience import audit, rows
from ..schemas import StrictModel
from ..security import Principal, current_principal

router = APIRouter(prefix="/api/v1", tags=["notifications"])


class Preferences(StrictModel):
    discord_enabled: bool = True
    security_enabled: bool = True
    transfers_enabled: bool = True
    incoming_enabled: bool = True
    outgoing_enabled: bool = True
    organizations_enabled: bool = True


class DeliveryResult(StrictModel):
    lease_token: uuid.UUID
    outcome: Literal["sent", "retry", "failed"]
    error: (
        Literal["dm_closed", "rate_limited", "discord_unavailable", "recipient_unavailable", "delivery_unknown"] | None
    ) = None
    message_id: Annotated[str | None, Field(max_length=30, pattern=r"^[0-9]+$")] = None
    retry_after: Annotated[int, Field(ge=0, le=3600)] = 0


async def bot_service(request: Request, settings: Settings = Depends(get_settings)) -> None:
    value = request.headers.get("x-horizon-bot-token", "")
    expected = settings.discord_bot_internal_token.get_secret_value()
    if not value or not hmac.compare_digest(value, expected):
        raise HTTPException(401, "Требуется авторизация сервиса уведомлений")


async def available(session: AsyncSession) -> bool:
    return bool(
        await session.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM auth.notification_worker_status WHERE last_seen_at>now()-interval '2 minutes')"
            )
        )
    )


@router.get("/me/notification-settings", response_model=Preferences)
async def preferences(
    principal: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    if principal.via_api_key:
        raise HTTPException(403, "Настройки доступны в кабинете или боте")
    found = await rows(
        session,
        "SELECT * FROM auth.notification_preferences WHERE user_id=:user",
        {"user": uuid.UUID(principal.user_id)},
    )
    return {k: found[0][k] for k in Preferences.model_fields} if found else Preferences().model_dump()


@router.put("/me/notification-settings", response_model=Preferences)
async def update_preferences(
    body: Preferences, principal: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    if principal.via_api_key:
        raise HTTPException(403, "Настройки доступны в кабинете или боте")
    values = {"user": uuid.UUID(principal.user_id), **body.model_dump()}
    await session.execute(
        text("INSERT INTO auth.notification_preferences(user_id) VALUES(:user) ON CONFLICT DO NOTHING"), values
    )
    fields = ",".join(k + "=:" + k for k in Preferences.model_fields)
    await session.execute(text("UPDATE auth.notification_preferences SET " + fields + " WHERE user_id=:user"), values)
    audit(session, principal.user_id, "notifications.settings", principal.user_id, body.model_dump())
    await session.commit()
    return body.model_dump()


@router.get("/me/notification-deliveries")
async def deliveries(
    offset: int = Query(0, ge=0, le=1000000),
    limit: int = Query(20, ge=1, le=100),
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if principal.via_api_key:
        raise HTTPException(403, "Статус доступен в кабинете или боте")
    found = await rows(
        session,
        """SELECT d.id,n.title,d.status,d.attempts,d.last_error,d.created_at,d.sent_at
      FROM auth.discord_outbox d JOIN auth.user_notifications n ON n.id=d.notification_id
      WHERE d.user_id=:user ORDER BY d.created_at DESC,d.id DESC LIMIT :limit OFFSET :offset""",
        {"user": uuid.UUID(principal.user_id), "limit": limit + 1, "offset": offset},
    )
    return {
        "items": found[:limit],
        "next_offset": offset + limit if len(found) > limit else None,
        "worker_online": await available(session),
    }


@router.post("/internal/notifications/claim", dependencies=[Depends(bot_service)], include_in_schema=False)
async def claim(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await session.execute(
        text("""INSERT INTO auth.notification_worker_status(id,last_seen_at) VALUES(true,now())
      ON CONFLICT(id) DO UPDATE SET last_seen_at=now()""")
    )
    found = await rows(
        session,
        """SELECT d.*,e.external_id discord_id,COALESCE(p.discord_enabled,true) enabled,
      COALESCE(p.security_enabled,true) security,COALESCE(p.transfers_enabled,true) transfers,
      COALESCE(p.incoming_enabled,true) incoming,COALESCE(p.outgoing_enabled,true) outgoing,
      COALESCE(p.organizations_enabled,true) organizations
      FROM auth.discord_outbox d LEFT JOIN auth.external_identities e ON e.user_id=d.user_id AND e.provider='discord'
      LEFT JOIN auth.notification_preferences p ON p.user_id=d.user_id
      WHERE (d.status='pending' AND d.available_at<=now()) OR (d.status='sending' AND d.lease_until<now())
      ORDER BY d.available_at,d.id LIMIT 50 FOR UPDATE OF d SKIP LOCKED""",
    )
    result = []
    for item in found:
        categories = item["payload"].get("categories") or [item["category"]]
        permitted = item["critical"] or (
            item["enabled"]
            and (
                not (item["payload"].get("operation_id") or item["payload"].get("payment_request_id"))
                or item["transfers"]
            )
            and any(bool(item.get(category, True)) for category in categories)
        )
        if not permitted or not item["discord_id"] or item["attempts"] >= 6:
            await session.execute(
                text(
                    "UPDATE auth.discord_outbox SET status=:status,last_error=:error,lease_token=NULL,lease_until=NULL WHERE id=:id"
                ),
                {
                    "id": item["id"],
                    "status": "suppressed" if not permitted else "failed",
                    "error": None
                    if not permitted
                    else "recipient_unavailable"
                    if not item["discord_id"]
                    else "delivery_unknown",
                },
            )
            continue
        token = uuid.uuid4()
        await session.execute(
            text(
                "UPDATE auth.discord_outbox SET status='sending',attempts=attempts+1,lease_token=:token,lease_until=now()+interval '90 seconds' WHERE id=:id"
            ),
            {"id": item["id"], "token": token},
        )
        result.append(
            {
                "id": item["id"],
                "lease_token": token,
                "discord_id": item["discord_id"],
                "nonce": str(item["id"].int >> 64),
                "payload": item["payload"],
            }
        )
        break  # Lease one item; other candidates are only examined for suppression.
    await session.commit()
    return {"items": result}


@router.post(
    "/internal/notifications/{delivery_id}/result", dependencies=[Depends(bot_service)], include_in_schema=False
)
async def result(
    delivery_id: uuid.UUID, body: DeliveryResult, session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    record = await rows(
        session,
        "SELECT attempts FROM auth.discord_outbox WHERE id=:id AND status='sending' AND lease_token=:token FOR UPDATE",
        {"id": delivery_id, "token": body.lease_token},
    )
    if not record:
        raise HTTPException(409, "Доставка уже обработана или её аренда изменилась")
    attempts = record[0]["attempts"]
    state = "sent" if body.outcome == "sent" else "pending" if body.outcome == "retry" and attempts < 6 else "failed"
    await session.execute(
        text("""UPDATE auth.discord_outbox SET status=:state,last_error=:error,message_id=:message,
      sent_at=CASE WHEN :state='sent' THEN now() ELSE NULL END,lease_token=NULL,lease_until=NULL,
      available_at=now()+make_interval(secs=>:delay) WHERE id=:id"""),
        {
            "id": delivery_id,
            "state": state,
            "error": body.error,
            "message": body.message_id,
            "delay": max(body.retry_after, min(3600, 5 * 2**attempts)),
        },
    )
    await session.commit()
    return {"status": state}

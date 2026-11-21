"""Authenticated coordinator boundary; all balances and physical states belong to C++ core."""

from __future__ import annotations

import hmac
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..database import get_session
from ..dependencies import get_core, request_ids
from ..experience import rows
from ..grpc.client import GrpcBankingClient
from ..rbac import permissions
from ..schemas import PositiveAmount, StrictModel
from ..security import Principal, require_admin, require_scope

router = APIRouter(prefix="/api/v1", tags=["ATM"])
Nickname = Annotated[str, Field(pattern=r"^[A-Za-z0-9_]{3,16}$")]


def service(request: Request, settings: Settings = Depends(get_settings)) -> None:
    token = settings.atm_service_token.get_secret_value()
    if not token or not hmac.compare_digest(
        request.headers.get("authorization", "").encode(), ("Bearer " + token).encode()
    ):
        raise HTTPException(401, "Требуется авторизация координатора банкоматов")


class Identity(StrictModel):
    nickname: Nickname
    player_uuid: uuid.UUID
    instance_id: uuid.UUID


class Physical(Identity):
    request_key: uuid.UUID
    kind: Literal["withdraw", "deposit", "collection"]
    quantity: int = Field(strict=True, ge=1, le=2304)


class Transfer(Identity):
    request_key: uuid.UUID
    recipient: Nickname
    amount: PositiveAmount


class Position(StrictModel):
    x: float = Field(allow_inf_nan=False, ge=-30000000, le=30000000)
    y: float = Field(allow_inf_nan=False, ge=-2048, le=4096)
    z: float = Field(allow_inf_nan=False, ge=-30000000, le=30000000)


class Observation(Position):
    uuid: uuid.UUID
    dimension: str = Field(max_length=100)
    age_ms: int = Field(ge=0, le=60000)


class Device(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    username: Nickname
    master: bool
    connected: bool
    dimension: str = Field(max_length=100)
    position: Position
    observations: dict[str, Observation]
    inventory: int = Field(ge=0, le=2304)
    free_space: int = Field(ge=0, le=2304)
    error: str = Field(max_length=500)
    container: dict[str, Any]


class Runtime(StrictModel):
    instance_id: uuid.UUID
    max_amount: int = Field(ge=1, le=2304)
    player_distance: float = Field(gt=0, le=32, allow_inf_nan=False)


class Heartbeat(StrictModel):
    instance_id: uuid.UUID
    devices: list[Device]


class Evidence(StrictModel):
    inventory_before: int = Field(ge=0, le=2304)
    container_before: int = Field(ge=0, le=3456)
    inventory_after: int | None = Field(default=None, ge=0, le=2304)
    container_after: int | None = Field(default=None, ge=0, le=3456)


class Action(StrictModel):
    instance_id: uuid.UUID
    evidence: Evidence | None = None
    error: str = Field(default="", max_length=500)


class Availability(StrictModel):
    enabled: bool


class Resolve(StrictModel):
    decision: Literal["settle", "cancel"]
    reason: str = Field(min_length=5, max_length=500)
    confirmed: Literal[True]


async def linked(session: AsyncSession, body: Identity) -> str:
    user = (
        await session.execute(
            text(
                "SELECT u.id FROM auth.minecraft_verified_links m "
                "JOIN auth.application_users u ON u.id=m.user_id WHERE lower(m.nickname)=lower(:n) "
                "AND m.player_uuid=:p AND u.status='active' AND u.erased_at IS NULL "
                "AND u.processing_restricted_at IS NULL"
            ),
            {"n": body.nickname, "p": body.player_uuid},
        )
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(403, "Привяжите Minecraft-аккаунт в настройках Banking System. Ник и UUID должны совпадать.")
    return str(user)


async def dispatch(
    core: GrpcBankingClient, request: Request, action: str, payload: dict[str, Any], target: str = ""
) -> dict[str, Any]:
    return await core.atm(action, target, payload, *request_ids(request))


@router.get("/atm/status")
async def status(
    request: Request,
    principal: Principal = Depends(require_scope("profile.read")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await dispatch(core, request, "status", {})


@router.post("/internal/atm/lease", dependencies=[Depends(service)])
async def lease(body: Runtime, request: Request, core: GrpcBankingClient = Depends(get_core)) -> dict[str, Any]:
    return await dispatch(core, request, "lease", body.model_dump(mode="json"))


@router.post("/internal/atm/heartbeat", dependencies=[Depends(service)])
async def heartbeat(
    body: Heartbeat,
    request: Request,
    core: GrpcBankingClient = Depends(get_core),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    linked = await rows(
        session,
        "SELECT l.player_uuid,l.nickname FROM auth.minecraft_verified_links l "
        "JOIN auth.application_users u ON u.id=l.user_id WHERE u.status='active' AND u.erased_at IS NULL AND u.processing_restricted_at IS NULL",
    )
    allowed = {(str(item["player_uuid"]), item["nickname"].lower()) for item in linked}
    payload = body.model_dump(mode="json")
    for device in payload["devices"]:
        device["observations"] = {
            name: value
            for name, value in device["observations"].items()
            if (str(value["uuid"]), name.lower()) in allowed and value["age_ms"] <= 3000
        }
    return await dispatch(core, request, "heartbeat", payload)


class PrivacyAck(StrictModel):
    blocked: bool = Field(strict=True)


@router.get("/internal/atm/privacy/tasks", dependencies=[Depends(service)])
async def privacy_tasks(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return {
        "items": await rows(
            session,
            "SELECT case_id,user_id,nicknames FROM auth.privacy_device_tasks WHERE acknowledged_at IS NULL ORDER BY created_at LIMIT 50",
        )
    }


@router.post("/internal/atm/privacy/tasks/{ident}", dependencies=[Depends(service)])
async def privacy_ack(
    ident: uuid.UUID, body: PrivacyAck, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    await session.execute(
        text(
            "UPDATE auth.privacy_device_tasks SET blocked=:blocked,acknowledged_at=CASE WHEN :blocked THEN NULL ELSE now() END,"
            "nicknames=CASE WHEN :blocked THEN nicknames ELSE '{}'::text[] END WHERE case_id=:id AND acknowledged_at IS NULL"
        ),
        {"id": ident, "blocked": body.blocked},
    )
    await session.commit()
    return {"accepted": True}


@router.post("/internal/atm/capabilities", dependencies=[Depends(service)])
async def capabilities(body: Identity, session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    granted = await permissions(session, await linked(session, body))
    return {
        "can_collect": "admin.atm.collect" in granted,
        "can_test": bool(granted & {"admin.atm.read", "admin.atm.manage", "admin.atm.collect"}),
    }


@router.post("/internal/atm/balance", dependencies=[Depends(service)])
async def balance(
    body: Identity,
    request: Request,
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await dispatch(
        core, request, "balance", {**body.model_dump(mode="json"), "user_id": await linked(session, body)}
    )


@router.post("/internal/atm/transfers", dependencies=[Depends(service)])
async def transfer(
    body: Transfer,
    request: Request,
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    user = await linked(session, body)
    recipient = (
        await session.execute(
            text(
                "SELECT u.id FROM auth.minecraft_verified_links m JOIN auth.application_users u ON u.id=m.user_id "
                "WHERE lower(m.nickname)=lower(:n) AND u.status='active'"
            ),
            {"n": body.recipient},
        )
    ).scalar_one_or_none()
    if recipient is None:
        raise HTTPException(404, "Получатель не найден или не привязал Minecraft-аккаунт")
    return await dispatch(
        core, request, "transfer", {**body.model_dump(mode="json"), "user_id": user, "recipient_id": str(recipient)}
    )


@router.post("/internal/atm/operations", dependencies=[Depends(service)])
async def prepare(
    body: Physical,
    request: Request,
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    user = await linked(session, body)
    if (
        body.kind == "collection"
        and not (
            await session.execute(
                text("SELECT 1 FROM auth.effective_permissions WHERE user_id=:u AND permission='admin.atm.collect'"),
                {"u": uuid.UUID(user)},
            )
        ).first()
    ):
        raise HTTPException(403, "Нет права на инкассацию банкомата")
    return await dispatch(core, request, "prepare", {**body.model_dump(mode="json"), "user_id": user})


@router.post("/internal/atm/operations/{operation_id}/{action}", dependencies=[Depends(service)])
async def operation_action(
    operation_id: uuid.UUID,
    action: Literal["get", "start", "confirm", "fail"],
    body: Action,
    request: Request,
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    if action in {"start", "confirm"} and body.evidence is None:
        raise HTTPException(422, "Необходимо подтверждение инвентаря")
    if (
        action == "confirm"
        and body.evidence
        and (body.evidence.inventory_after is None or body.evidence.container_after is None)
    ):
        raise HTTPException(422, "Необходимо подтвердить результат перемещения")
    payload = body.model_dump(mode="json")
    payload["evidence"] = body.evidence.model_dump(exclude_none=True) if body.evidence else {}
    return await dispatch(core, request, action, payload, str(operation_id))


@router.get("/admin/atm")
async def admin_list(
    request: Request,
    device_id: str = "",
    status: Literal["", "CREATED", "RESERVED", "PROCESSING", "COMPLETED", "FAILED", "REQUIRES_REVIEW"] = "",
    offset: int = 0,
    principal: Principal = Depends(require_admin("admin.atm.read")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    if not 0 <= offset <= 1000000 or len(device_id) > 32:
        raise HTTPException(422, "Некорректный фильтр")
    return await dispatch(core, request, "admin.list", {"device_id": device_id, "status": status, "offset": offset})


@router.patch("/admin/atm/devices/{device_id}")
async def availability(
    device_id: str,
    body: Availability,
    request: Request,
    principal: Principal = Depends(require_admin("admin.atm.manage")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await dispatch(
        core, request, "admin.enabled", {"enabled": body.enabled, "admin_id": principal.user_id}, device_id
    )


@router.post("/admin/atm/operations/{operation_id}/resolve")
async def resolve(
    operation_id: uuid.UUID,
    body: Resolve,
    request: Request,
    principal: Principal = Depends(require_admin("admin.atm.manage")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await dispatch(
        core, request, "admin.resolve", {**body.model_dump(), "admin_id": principal.user_id}, str(operation_id)
    )

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.openapi.utils import get_openapi
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..dependencies import get_core, request_ids
from ..experience import audit, rows
from ..grpc.client import CoreError, GrpcBankingClient
from ..rbac import administration_lock
from ..schemas import StrictModel
from ..security import Principal, require_admin

router = APIRouter(prefix="/api/v1/admin", tags=["financial administration"])


class FinancialPermission(StrictModel):
    user_id: uuid.UUID
    account_id: uuid.UUID | None = None
    permission: Annotated[str, Field(min_length=1, max_length=50)]
    revoke: bool = False
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    confirmed: bool


class AccountState(StrictModel):
    state: Literal["active", "frozen", "closed"]
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    confirmed: bool


@router.get("/overview")
async def overview(
    request: Request,
    principal: Principal = Depends(require_admin("admin.overview")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "users": await session.scalar(text("SELECT count(*) FROM auth.application_users")),
        "core": "permission_required",
    }
    try:
        value.update(await core.administrative_query(principal.user_id, *request_ids(request), kind="overview"))
    except CoreError as error:
        if error.code != "PERMISSION_DENIED":
            value["core"] = "unavailable"
    value["recent_actions"] = await rows(
        session,
        "SELECT action,created_at FROM auth.audit_events WHERE actor_id=:id ORDER BY created_at DESC LIMIT 6",
        {"id": uuid.UUID(principal.user_id)},
    )
    return value


@router.get("/accounts")
async def accounts(
    request: Request,
    q: str = Query("", max_length=100),
    owner_id: uuid.UUID | None = None,
    cursor: uuid.UUID | None = None,
    limit: int = Query(25, ge=1, le=100),
    principal: Principal = Depends(require_admin("admin.accounts.read")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    result = await core.administrative_query(
        principal.user_id,
        *request_ids(request),
        kind="accounts",
        query=q,
        target_id=str(owner_id) if owner_id else "",
        cursor=str(cursor) if cursor else "",
        limit=limit,
    )
    ids = [uuid.UUID(x["owner_id"]) for x in result["items"] if x.get("owner_id")]
    owners = (
        await rows(
            session,
            "SELECT id,bank_username,display_name FROM auth.application_users WHERE id=ANY(CAST(:ids AS uuid[]))",
            {"ids": ids},
        )
        if ids
        else []
    )
    by_id = {str(x["id"]): x["bank_username"] or x["display_name"] for x in owners}
    for account in result["items"]:
        account["owner"] = by_id.get(account.get("owner_id"), account.get("organization") or "Банковский счёт")
    return result


@router.get("/transactions")
async def transactions(
    request: Request,
    q: str = Query("", max_length=100),
    account_id: uuid.UUID | None = None,
    cursor: uuid.UUID | None = None,
    limit: int = Query(25, ge=1, le=100),
    operation_type: str = Query("", max_length=30),
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    principal: Principal = Depends(require_admin("admin.transactions.read")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await core.administrative_query(
        principal.user_id,
        *request_ids(request),
        kind="operations",
        query=q,
        target_id=str(account_id) if account_id else "",
        cursor=str(cursor) if cursor else "",
        limit=limit,
        operation_type=operation_type,
        from_date=from_date.isoformat() if from_date else "",
        to_date=to_date.isoformat() if to_date else "",
    )


@router.get("/financial-permissions")
async def financial_permissions(
    request: Request,
    user_id: uuid.UUID | None = None,
    cursor: uuid.UUID | None = None,
    limit: int = Query(25, ge=1, le=100),
    principal: Principal = Depends(require_admin("admin.finance.manage")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await core.administrative_query(
        principal.user_id,
        *request_ids(request),
        kind="permissions",
        target_id=str(user_id) if user_id else "",
        cursor=str(cursor) if cursor else "",
        limit=limit,
    )


@router.post("/financial-permissions")
async def change_permission(
    body: FinancialPermission,
    request: Request,
    principal: Principal = Depends(require_admin("admin.finance.manage")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await administration_lock(session, principal.user_id, "admin.finance.manage")
    if not await session.scalar(text("SELECT id FROM auth.application_users WHERE id=:id"), {"id": body.user_id}):
        raise HTTPException(404, "Пользователь не найден")
    result = await core.administrative_change(
        principal.user_id,
        *request_ids(request),
        kind="permission",
        target_id=str(body.user_id),
        account_id=str(body.account_id) if body.account_id else "",
        permission=body.permission,
        revoke=body.revoke,
        reason=body.reason,
        confirmed=body.confirmed,
    )
    audit(session, principal.user_id, "finance.permission", str(body.user_id), body.model_dump(mode="json"))
    await session.commit()
    return result


@router.post("/accounts/{account_id}/state")
async def state(
    account_id: uuid.UUID,
    body: AccountState,
    request: Request,
    principal: Principal = Depends(require_admin("admin.accounts.manage")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await administration_lock(session, principal.user_id, "admin.accounts.manage")
    value = await core.administrative_change(
        principal.user_id,
        *request_ids(request),
        kind="state",
        account_id=str(account_id),
        state=body.state,
        reason=body.reason,
        confirmed=body.confirmed,
    )
    audit(session, principal.user_id, "account.state", str(account_id), body.model_dump())
    await session.commit()
    return value


@router.get("/openapi.json", include_in_schema=False)
async def administrative_schema(
    request: Request, principal: Principal = Depends(require_admin("admin.overview"))
) -> dict[str, Any]:
    schema = get_openapi(title="Banking System — административный API", version="2.2.0", routes=request.app.routes)
    schema["paths"] = {k: v for k, v in schema["paths"].items() if k.startswith("/api/v1/admin/")}
    return schema

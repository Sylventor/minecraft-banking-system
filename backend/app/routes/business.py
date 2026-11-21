"""Organization-only analytics and interactive payroll. The API never writes finance."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..business_calendar import analytics_period, next_occurrence
from ..business_csv import safe_csv
from ..database import get_session
from ..dependencies import get_core, request_ids
from ..grpc.client import GrpcBankingClient
from ..schemas import IdempotencyKey, PositiveAmount, StrictModel
from ..security import Principal, current_principal

router = APIRouter(prefix="/api/v1/organizations/{organization_id}", tags=["business analytics and payroll"])
BUSINESS_PERMISSIONS = [
    "analytics.read",
    "analytics.export",
    "analytics.categorize",
    "payroll.read",
    "payroll.employees.manage",
    "payroll.salaries.manage",
    "payroll.batches.create",
    "payroll.approve",
    "payroll.execute",
    "payroll.automatic.manage",
]


class BusinessModel(StrictModel):
    @field_validator("*", mode="before")
    @classmethod
    def valid_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            if "\x00" in value:
                raise ValueError("NUL is not allowed")
            try:
                value.encode("utf-8", errors="strict")
            except UnicodeEncodeError as error:
                raise ValueError("Valid UTF-8 required") from error
        return value


class EmployeeCreate(BusinessModel):
    user_id: uuid.UUID
    account_id: uuid.UUID
    salary: PositiveAmount
    frequency: Literal["weekly", "biweekly", "monthly"] = "monthly"
    position: Annotated[str, Field(max_length=100)] = ""
    note: Annotated[str, Field(max_length=500)] = ""


class EmployeeUpdate(BusinessModel):
    account_id: uuid.UUID | None = None
    salary: PositiveAmount | None = None
    frequency: Literal["weekly", "biweekly", "monthly"] | None = None
    position: Annotated[str | None, Field(max_length=100)] = None
    note: Annotated[str | None, Field(max_length=500)] = None
    active: bool | None = None


class BatchItem(BusinessModel):
    employee_id: uuid.UUID
    amount: PositiveAmount | None = None


class BatchCreate(BusinessModel):
    source_account_id: uuid.UUID
    description: Annotated[str, Field(min_length=1, max_length=450)]
    items: list[BatchItem] = Field(min_length=1, max_length=100)


class Confirmation(BusinessModel):
    confirmed: Literal[True]


class ScheduleCreate(BatchCreate):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    frequency: Literal["weekly", "biweekly", "monthly"]
    weekday: int = Field(default=1, ge=1, le=7)
    month_day: int = Field(default=1, ge=1, le=31)
    local_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    timezone: str = Field(min_length=1, max_length=64)
    mode: Literal["manual", "automatic"]
    max_amount: PositiveAmount
    confirmed: Literal[True]

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Unknown IANA timezone") from error
        return value

    @field_validator("local_time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        time.fromisoformat(value)
        return value


class ScheduleApproval(Confirmation):
    revision: int = Field(ge=1)


class ScheduleState(BusinessModel):
    status: Literal["active", "paused", "disabled"]


class CategoryCreate(BusinessModel):
    name: str = Field(min_length=1, max_length=80)


class CategoryAssign(BusinessModel):
    category: str = Field(min_length=1, max_length=80)


async def authorize(
    principal: Principal,
    core: GrpcBankingClient,
    request: Request,
    org: uuid.UUID,
    permission: str,
    *,
    analytics: bool = False,
) -> None:
    if principal.via_api_key:
        if not analytics or permission not in principal.scopes:
            raise HTTPException(403, "Зарплаты требуют интерактивной сессии; аналитика — отдельного scope")
    if principal.via_bot:
        raise HTTPException(403, "Управление бизнесом доступно в личном кабинете")
    rights = await core.business(principal.user_id, str(org), "permissions", "", {}, *request_ids(request))
    if permission not in rights["permissions"]:
        raise HTTPException(403, "Нет полномочия организации: " + permission)


async def decorate(session: AsyncSession, value: dict[str, Any]) -> dict[str, Any]:
    candidates = value.get("items", [value])
    for item in candidates:
        if item.get("user_id"):
            item["user_name"] = (
                await session.scalar(
                    text("SELECT COALESCE(bank_username,display_name) FROM auth.application_users WHERE id=:id"),
                    {"id": uuid.UUID(item["user_id"])},
                )
                or "Пользователь банка"
            )
        if "items" in item:
            await decorate(session, item)
    return value


@router.get("/business-permissions")
async def business_permissions(
    organization_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(current_principal),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    if principal.via_api_key or principal.via_bot:
        raise HTTPException(403, "Интерактивная сессия обязательна")
    return await core.business(principal.user_id, str(organization_id), "permissions", "", {}, *request_ids(request))


@router.get("/analytics/{kind}")
async def analytics(
    organization_id: uuid.UUID,
    kind: Literal["summary", "cashflow", "balance-history", "categories", "counterparties", "transactions", "export"],
    request: Request,
    period: Literal["today", "7d", "30d", "90d", "month", "previous_month"] = "30d",
    from_date: Annotated[datetime | None, Query(alias="from")] = None,
    to_date: Annotated[datetime | None, Query(alias="to")] = None,
    timezone: str = Query("UTC", max_length=64),
    account_id: uuid.UUID | None = None,
    interval: Literal["day", "week", "month"] = "day",
    category: str = Query("", max_length=80),
    limit: int = Query(25, ge=1, le=100),
    cursor: uuid.UUID | None = None,
    export_type: Literal["transactions", "cashflow", "summary", "payroll"] = "transactions",
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> Any:
    await authorize(
        principal,
        core,
        request,
        organization_id,
        "analytics.export" if kind == "export" else "analytics.read",
        analytics=True,
    )
    try:
        filters: dict[str, Any] = analytics_period(period, timezone, from_date, to_date)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise HTTPException(422, "Некорректный период или часовой пояс") from error
    filters.update(
        account_id=str(account_id) if account_id else "",
        interval=interval,
        category=category,
        limit=limit,
        cursor=str(cursor) if cursor else "",
    )
    if principal.via_api_key and account_id and str(account_id) not in (principal.account_ids or []):
        raise HTTPException(403, "Счёт не включён в API-ключ")

    async def get(part: str) -> dict[str, Any]:
        return await core.business(
            principal.user_id,
            str(organization_id),
            "analytics." + part,
            "",
            filters,
            *request_ids(request),
            allowed_accounts=sorted(principal.account_ids or []),
            scoped=principal.via_api_key,
        )

    if kind != "export":
        return {**await get(kind), "period": filters}
    if export_type == "summary":
        result = await get("summary")
        return safe_csv(
            ["Показатель", "Значение, ДБ", "Предыдущий период"],
            [[k, v, result["previous"].get(k)] for k, v in result["current"].items()],
        )
    if export_type == "cashflow":
        result = await get("cashflow")
        return safe_csv(
            ["Дата", "Поступления, ДБ", "Расходы, ДБ", "Операций", "Остаток, ДБ"],
            [[x["at"], x["incoming"], x["outgoing"], x["activity"], x["balance"]] for x in result["items"]],
        )
    if export_type == "payroll":
        await authorize(principal, core, request, organization_id, "payroll.read")
        result = await get("payroll")
        data = [
            [i["batch_id"], i["user_name"], i["amount"], i["status"], i.get("operation_id") or "", i["paid_at"]]
            for i in (await decorate(session, result))["items"]
        ]
        response = safe_csv(["Ведомость", "Сотрудник", "Сумма, ДБ", "Статус", "Операция", "Дата выплаты"], data)
        if result.get("next_cursor"):
            response.headers["X-Next-Cursor"] = result["next_cursor"]
        return response
    # Each export is a bounded, cursor-addressable page; no unbounded full-history read.
    result = await get("transactions")
    response = safe_csv(
        ["ID", "Дата", "Тип", "Категория", "Поток, ДБ", "Назначение"],
        [[x["id"], x["created_at"], x["type"], x["category"], x["amount"], x["reason"]] for x in result["items"]],
    )
    if result.get("next_cursor"):
        response.headers["X-Next-Cursor"] = result["next_cursor"]
    return response


@router.post("/analytics/categories", status_code=201)
async def create_category(
    organization_id: uuid.UUID,
    body: CategoryCreate,
    request: Request,
    principal: Principal = Depends(current_principal),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await authorize(principal, core, request, organization_id, "analytics.categorize")
    return await core.business(
        principal.user_id, str(organization_id), "category.create", "", body.model_dump(), *request_ids(request)
    )


@router.put("/analytics/transactions/{operation_id}/category")
async def categorize(
    organization_id: uuid.UUID,
    operation_id: uuid.UUID,
    body: CategoryAssign,
    request: Request,
    principal: Principal = Depends(current_principal),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await authorize(principal, core, request, organization_id, "analytics.categorize")
    return await core.business(
        principal.user_id,
        str(organization_id),
        "category.assign",
        str(operation_id),
        body.model_dump(),
        *request_ids(request),
    )


@router.get("/payroll/overview")
@router.get("/payroll/history")
async def payroll_read(
    organization_id: uuid.UUID,
    request: Request,
    limit: int = Query(25, ge=1, le=100),
    cursor: uuid.UUID | None = None,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await authorize(principal, core, request, organization_id, "payroll.read")
    return await decorate(
        session,
        await core.business(
            principal.user_id,
            str(organization_id),
            "payroll." + request.url.path.rsplit("/", 1)[-1],
            "",
            {"limit": limit, "cursor": str(cursor) if cursor else ""},
            *request_ids(request),
        ),
    )


@router.get("/payroll/employees")
async def employees(
    organization_id: uuid.UUID,
    request: Request,
    q: str = Query("", max_length=100),
    active: bool | None = None,
    limit: int = Query(25, ge=1, le=100),
    cursor: uuid.UUID | None = None,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await authorize(principal, core, request, organization_id, "payroll.read")
    users = (
        (
            await session.execute(
                text(
                    "SELECT id FROM auth.application_users WHERE position(lower(:q) in lower(COALESCE(bank_username,'')||' '||display_name))>0 LIMIT 100"
                ),
                {"q": q},
            )
        )
        .scalars()
        .all()
        if q
        else []
    )
    return await decorate(
        session,
        await core.business(
            principal.user_id,
            str(organization_id),
            "employee.list",
            "",
            {
                "q": q,
                "active": "all" if active is None else str(active).lower(),
                "limit": limit,
                "cursor": str(cursor) if cursor else "",
                "user_ids": [str(u) for u in users],
            },
            *request_ids(request),
        ),
    )


@router.post("/payroll/employees", status_code=201)
async def add_employee(
    organization_id: uuid.UUID,
    body: EmployeeCreate,
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await authorize(principal, core, request, organization_id, "payroll.employees.manage")
    if not await session.scalar(
        text("SELECT id FROM auth.application_users WHERE id=:id AND status='active'"), {"id": body.user_id}
    ):
        raise HTTPException(404, "Сотрудник не найден")
    return await decorate(
        session,
        await core.business(
            principal.user_id,
            str(organization_id),
            "employee.create",
            "",
            body.model_dump(mode="json"),
            *request_ids(request),
        ),
    )


@router.patch("/payroll/employees/{employee_id}")
async def edit_employee(
    organization_id: uuid.UUID,
    employee_id: uuid.UUID,
    body: EmployeeUpdate,
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await authorize(principal, core, request, organization_id, "payroll.employees.manage")
    return await decorate(
        session,
        await core.business(
            principal.user_id,
            str(organization_id),
            "employee.update",
            str(employee_id),
            body.model_dump(mode="json", exclude_none=True),
            *request_ids(request),
        ),
    )


@router.delete("/payroll/employees/{employee_id}")
async def disable_employee(
    organization_id: uuid.UUID,
    employee_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(current_principal),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await authorize(principal, core, request, organization_id, "payroll.employees.manage")
    return await core.business(
        principal.user_id,
        str(organization_id),
        "employee.update",
        str(employee_id),
        {"active": False},
        *request_ids(request),
    )


@router.get("/payroll/batches")
@router.get("/payroll/schedules")
async def payroll_list(
    organization_id: uuid.UUID,
    request: Request,
    status: str = Query("", max_length=30),
    limit: int = Query(25, ge=1, le=100),
    cursor: uuid.UUID | None = None,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await authorize(principal, core, request, organization_id, "payroll.read")
    kind = "batch" if request.url.path.endswith("batches") else "schedule"
    return await decorate(
        session,
        await core.business(
            principal.user_id,
            str(organization_id),
            kind + ".list",
            "",
            {"limit": limit, "cursor": str(cursor) if cursor else "", "status": status},
            *request_ids(request),
        ),
    )


@router.post("/payroll/batches", status_code=201)
async def create_batch(
    organization_id: uuid.UUID,
    body: BatchCreate,
    request: Request,
    idempotency_key: Annotated[IdempotencyKey, Header()],
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await authorize(principal, core, request, organization_id, "payroll.batches.create")
    return await decorate(
        session,
        await core.business(
            principal.user_id,
            str(organization_id),
            "batch.create",
            "",
            {**body.model_dump(mode="json", exclude_none=True), "creation_key": idempotency_key},
            *request_ids(request),
        ),
    )


@router.get("/payroll/batches/{batch_id}")
async def batch_detail(
    organization_id: uuid.UUID,
    batch_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await authorize(principal, core, request, organization_id, "payroll.read")
    return await decorate(
        session,
        await core.business(
            principal.user_id, str(organization_id), "batch.get", str(batch_id), {}, *request_ids(request)
        ),
    )


@router.post("/payroll/batches/{batch_id}/{action}")
async def batch_action(
    organization_id: uuid.UUID,
    batch_id: uuid.UUID,
    action: Literal["submit", "approve", "execute", "retry", "cancel"],
    body: Confirmation,
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    permission = (
        "payroll.approve"
        if action == "approve"
        else "payroll.execute"
        if action in {"execute", "retry"}
        else "payroll.batches.create"
    )
    await authorize(principal, core, request, organization_id, permission)
    return await decorate(
        session,
        await core.business(
            principal.user_id,
            str(organization_id),
            "batch." + action,
            str(batch_id),
            body.model_dump(),
            *request_ids(request),
        ),
    )


@router.post("/payroll/schedules", status_code=201)
async def create_schedule(
    organization_id: uuid.UUID,
    body: ScheduleCreate,
    request: Request,
    principal: Principal = Depends(current_principal),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await authorize(principal, core, request, organization_id, "payroll.automatic.manage")
    data = body.model_dump(mode="json", exclude_none=True)
    data["next_run_at"] = next_occurrence(data, datetime.now(UTC)).isoformat()
    return await core.business(
        principal.user_id, str(organization_id), "schedule.create", "", data, *request_ids(request)
    )


@router.get("/payroll/schedules/{schedule_id}")
async def schedule_detail(
    organization_id: uuid.UUID,
    schedule_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await authorize(principal, core, request, organization_id, "payroll.read")
    return await decorate(
        session,
        await core.business(
            principal.user_id, str(organization_id), "schedule.get", str(schedule_id), {}, *request_ids(request)
        ),
    )


@router.patch("/payroll/schedules/{schedule_id}")
async def update_schedule(
    organization_id: uuid.UUID,
    schedule_id: uuid.UUID,
    body: ScheduleCreate | ScheduleState,
    request: Request,
    principal: Principal = Depends(current_principal),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await authorize(principal, core, request, organization_id, "payroll.automatic.manage")
    data = body.model_dump(mode="json", exclude_none=True)
    action = "schedule.state" if isinstance(body, ScheduleState) else "schedule.update"
    if action == "schedule.update":
        data["next_run_at"] = next_occurrence(data, datetime.now(UTC)).isoformat()
    return await core.business(
        principal.user_id, str(organization_id), action, str(schedule_id), data, *request_ids(request)
    )


@router.post("/payroll/schedules/{schedule_id}/approve")
async def approve_schedule(
    organization_id: uuid.UUID,
    schedule_id: uuid.UUID,
    body: ScheduleApproval,
    request: Request,
    principal: Principal = Depends(current_principal),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await authorize(principal, core, request, organization_id, "payroll.automatic.manage")
    return await core.business(
        principal.user_id,
        str(organization_id),
        "schedule.approve",
        str(schedule_id),
        body.model_dump(),
        *request_ids(request),
    )


@router.delete("/payroll/schedules/{schedule_id}")
async def disable_schedule(
    organization_id: uuid.UUID,
    schedule_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(current_principal),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await authorize(principal, core, request, organization_id, "payroll.automatic.manage")
    return await core.business(
        principal.user_id,
        str(organization_id),
        "schedule.state",
        str(schedule_id),
        {"status": "disabled"},
        *request_ids(request),
    )

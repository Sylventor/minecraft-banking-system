"""Payment proposals. Only an interactive payer can authorize settlement."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..dependencies import get_core, request_ids
from ..grpc.client import CoreError, GrpcBankingClient, fingerprint
from ..schemas import IdempotencyKey, PositiveAmount, StrictModel
from ..security import Principal, require_resource, require_scope

router = APIRouter(prefix="/api/v1/payment-requests", tags=["payment requests"])
Status = Literal["pending", "paid", "declined", "cancelled", "expired"]


class PaymentRequestView(StrictModel):
    id: uuid.UUID
    creator_id: uuid.UUID
    organization_id: uuid.UUID | None
    payer_id: uuid.UUID
    recipient_account_id: uuid.UUID
    amount: PositiveAmount
    currency: Literal["DB"]
    description: str
    status: Status
    external_id: str | None
    metadata: dict[str, Any]
    created_at: datetime
    expires_at: datetime
    paid_at: datetime | None
    transaction_id: uuid.UUID | None
    source_account_id: uuid.UUID | None
    cancelled_at: datetime | None
    declined_at: datetime | None
    merchant_name: str
    recipient_account_name: str
    recipient_public_number: str
    creator_name: str
    payer_name: str


class PaymentRequestPage(StrictModel):
    items: list[PaymentRequestView]
    next_offset: int | None
    total: int = 0


class CreateRequest(StrictModel):
    payer_id: uuid.UUID
    recipient_account_id: uuid.UUID
    amount: PositiveAmount
    description: Annotated[str, Field(min_length=1, max_length=500)]
    expires_in: Annotated[int | None, Field(ge=60, le=2592000)] = None
    expires_at: datetime | None = None
    external_id: Annotated[str | None, Field(min_length=1, max_length=120)] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("description", "external_id")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        if value is not None:
            value.encode("utf-8")
        if value is not None and (not value.strip() or any(ord(c) < 32 for c in value)):
            raise ValueError("Text must not be empty or contain control characters")
        return value.strip() if value else value

    @model_validator(mode="after")
    def bounded(self) -> CreateRequest:
        if self.expires_in is not None and self.expires_at is not None:
            raise ValueError("Use expires_at or expires_in, not both")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at requires a timezone")
        try:
            value = json.dumps(self.metadata, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError) as error:
            raise ValueError("Invalid JSON metadata") from error
        if len(value.encode()) > 8192:
            raise ValueError("metadata exceeds 8 KiB")
        return self


class PayRequest(StrictModel):
    source_account_id: uuid.UUID
    confirmed: Literal[True]


def binding(principal: Principal) -> dict[str, Any]:
    if principal.via_api_key:
        if not principal.organization_id:
            raise HTTPException(403, "Для магазина требуется ключ конкретной организации")
        return {
            "key_id": principal.api_key_id,
            "organization_id": principal.organization_id,
            "account_ids": sorted(principal.account_ids or []),
        }
    return {}


async def decorate(session: AsyncSession, item: dict[str, Any]) -> dict[str, Any]:
    for field in ("payer", "creator"):
        item[field + "_name"] = (
            await session.scalar(
                text("SELECT COALESCE(bank_username,display_name) FROM auth.application_users WHERE id=:id"),
                {"id": uuid.UUID(item[field + "_id"])},
            )
            or "Пользователь банка"
        )
    return item


@router.post("", status_code=201, response_model=PaymentRequestView)
async def create(
    body: CreateRequest,
    request: Request,
    idempotency_key: Annotated[IdempotencyKey, Header()],
    principal: Principal = Depends(require_scope("payment_requests.create")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    require_resource(principal, str(body.recipient_account_id))
    if not await session.scalar(
        text("SELECT id FROM auth.application_users WHERE id=:id AND status='active'"), {"id": body.payer_id}
    ):
        raise HTTPException(404, "Плательщик не найден")
    payload = body.model_dump(mode="json")
    try:
        result = await core.payment_requests(
            principal.user_id,
            *request_ids(request),
            action="create",
            **binding(principal),
            payer_id=str(body.payer_id),
            recipient_account_id=str(body.recipient_account_id),
            amount=body.amount,
            description=body.description,
            expires_in=body.expires_in or (0 if body.expires_at else 3600),
            expires_at=body.expires_at.isoformat() if body.expires_at else "",
            external_id=body.external_id or "",
            metadata=json.dumps(body.metadata, sort_keys=True, ensure_ascii=True, allow_nan=False),
            idempotency_key=idempotency_key,
            fingerprint=fingerprint("payment_request.create", principal.user_id, payload),
        )
    except CoreError as error:
        if error.code == "RESOURCE_EXHAUSTED":
            raise HTTPException(429, "Лимит запросов на оплату превышен. Повторите позже.") from error
        raise
    return await decorate(session, result)


@router.get("/incoming", response_model=PaymentRequestPage)
@router.get("/outgoing", response_model=PaymentRequestPage)
@router.get("/history", response_model=PaymentRequestPage)
async def listing(
    request: Request,
    status: Status | None = None,
    external_id: str = Query("", max_length=120),
    organization_id: uuid.UUID | None = None,
    order: Literal["asc", "desc"] = "desc",
    offset: int = Query(0, ge=0, le=1000000),
    limit: int = Query(25, ge=1, le=100),
    principal: Principal = Depends(require_scope("payment_requests.read")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    bound = binding(principal)
    if principal.via_api_key and not request.url.path.endswith("/outgoing"):
        raise HTTPException(403, "Ключ магазина может читать только собственные исходящие запросы")
    if organization_id:
        if principal.via_api_key and str(organization_id) != principal.organization_id:
            raise HTTPException(403, "Чужая организация недоступна")
        bound["organization_id"] = str(organization_id)
    result = await core.payment_requests(
        principal.user_id,
        *request_ids(request),
        action="list",
        **bound,
        direction=request.url.path.rsplit("/", 1)[-1],
        status=status or "",
        external_id=external_id,
        order=order,
        offset=offset,
        limit=limit,
    )
    result["items"] = [await decorate(session, item) for item in result["items"]]
    return result


@router.get("/{request_id}", response_model=PaymentRequestView)
async def detail(
    request_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_scope("payment_requests.read")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    result = await core.payment_requests(
        principal.user_id, *request_ids(request), action="get", id=str(request_id), **binding(principal)
    )
    return await decorate(session, result)


@router.post("/{request_id}/pay", response_model=PaymentRequestView)
async def pay(
    request_id: uuid.UUID,
    body: PayRequest,
    request: Request,
    idempotency_key: Annotated[IdempotencyKey, Header()],
    principal: Principal = Depends(require_scope("payment_requests.read")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    if principal.via_api_key or principal.via_bot:
        raise HTTPException(403, "Оплата требует подтверждения плательщика в личном кабинете")
    result = await core.payment_requests(
        principal.user_id,
        *request_ids(request),
        action="pay",
        id=str(request_id),
        source_account_id=str(body.source_account_id),
        confirmed=body.confirmed,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint(
            "payment_request.pay", principal.user_id, {"id": str(request_id), **body.model_dump(mode="json")}
        ),
    )
    return await decorate(session, result)


@router.post("/{request_id}/decline", response_model=PaymentRequestView)
async def decline(
    request_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_scope("payment_requests.read")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    if principal.via_api_key or principal.via_bot:
        raise HTTPException(403, "Отклонить запрос может только плательщик в кабинете")
    result = await core.payment_requests(principal.user_id, *request_ids(request), action="decline", id=str(request_id))
    return await decorate(session, result)


@router.post("/{request_id}/cancel", response_model=PaymentRequestView)
async def cancel(
    request_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_scope("payment_requests.cancel")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    result = await core.payment_requests(
        principal.user_id, *request_ids(request), action="cancel", id=str(request_id), **binding(principal)
    )
    return await decorate(session, result)

"""Real core-settled exchange; API keys must opt into independent exchange scopes."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..dependencies import get_core, request_ids
from ..experience import lookup_limit
from ..grpc.client import GrpcBankingClient
from ..schemas import IdempotencyKey, PositiveAmount
from ..security import Principal, require_admin, require_scope
from .business import BusinessModel

router = APIRouter(prefix="/api/v1", tags=["stock exchange"])
PUBLIC_ACTOR = "00000000-0000-0000-0000-000000000025"
Ticker = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9]{1,9}$")]
Quantity = Annotated[int, Field(strict=True, ge=1, le=1000000000)]


class Confirm(BusinessModel):
    confirmed: Literal[True]


class TickerAvailability(BusinessModel):
    available: bool


class OrderInput(Confirm):
    ticker: Ticker
    account_id: uuid.UUID
    side: Literal["BUY", "SELL"]
    kind: Literal["limit", "market"]
    quantity: Quantity
    price: PositiveAmount
    budget: PositiveAmount | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def bounds(self) -> "OrderInput":
        if self.expires_at is not None and (
            self.expires_at.tzinfo is None
            or not datetime.now(UTC) < self.expires_at <= datetime.now(UTC) + timedelta(days=90)
        ):
            raise ValueError("Expiry requires an offset and must be within 90 days")
        if self.kind == "market" and self.side == "BUY" and self.budget is None:
            raise ValueError("Market BUY requires a maximum budget and price ceiling")
        return self


class IssuerInput(Confirm):
    organization_id: uuid.UUID
    account_id: uuid.UUID
    ticker: Ticker
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    terms: str = Field(min_length=1, max_length=4000)
    rights: str = Field(min_length=1, max_length=2000)
    quantity: Quantity


class IPOInput(Confirm):
    quantity: Quantity
    price: PositiveAmount
    starts_at: datetime
    ends_at: datetime
    max_per_buyer: Quantity = 1000000000

    @model_validator(mode="after")
    def dates(self) -> "IPOInput":
        if (
            self.starts_at.tzinfo is None
            or self.ends_at.tzinfo is None
            or self.ends_at <= max(self.starts_at, datetime.now(UTC))
        ):
            raise ValueError("IPO requires ordered, offset-aware dates and a future end")
        return self


class IPOBuy(Confirm):
    account_id: uuid.UUID
    quantity: Quantity


class DividendInput(Confirm):
    stock_id: uuid.UUID
    account_id: uuid.UUID
    mode: Literal["total", "per_share"]
    amount: PositiveAmount
    record_at: datetime
    pay_at: datetime

    @model_validator(mode="after")
    def dates(self) -> "DividendInput":
        if self.record_at.tzinfo is None or self.pay_at.tzinfo is None or self.pay_at < self.record_at:
            raise ValueError("Offset-aware record/payment dates required")
        return self


class Moderation(Confirm):
    status: Literal["approved", "rejected", "active", "suspended", "delisted"]
    reason: str = Field(min_length=1, max_length=500)


class Additional(Confirm):
    quantity: Quantity
    reason: str = Field(min_length=1, max_length=500)


async def call(
    request: Request,
    core: GrpcBankingClient,
    action: str,
    payload: dict[str, Any],
    principal: Principal | None = None,
    target: str = "",
    org: str = "",
) -> dict[str, Any]:
    if principal and principal.via_bot:
        raise HTTPException(403, "Биржа доступна в кабинете или по отдельному API scope")
    if (
        principal
        and not principal.via_api_key
        and action in {"issuer.create", "ipo.create", "dividend.approve", "dividend.execute"}
        and (datetime.now(UTC) - request.state.authenticated_at).total_seconds() > 900
    ):
        raise HTTPException(403, "Повторно войдите через Discord для чувствительной операции")
    if (
        principal
        and principal.via_api_key
        and action
        not in {
            "read.orders",
            "read.order",
            "read.portfolio",
            "read.history",
            "read.dividend_history",
            "order.create",
            "order.cancel",
        }
    ):
        raise HTTPException(403, "Управление эмитентом и дивидендами требует интерактивной сессии")
    return await core.exchange(
        principal.user_id if principal else PUBLIC_ACTOR,
        action,
        target,
        org,
        payload,
        *request_ids(request),
        allowed_accounts=sorted(principal.account_ids or []) if principal else [],
        scoped=principal.via_api_key if principal else False,
    )


@router.get("/exchange/stocks")
async def stocks(
    request: Request,
    q: str = Query("", max_length=100),
    limit: int = Query(25, ge=1, le=100),
    cursor: uuid.UUID | None = None,
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await call(request, core, "read.stocks", {"q": q, "limit": limit, "cursor": str(cursor) if cursor else ""})


@router.get("/exchange/ticker-availability/{ticker}")
async def ticker_availability(
    ticker: Ticker,
    request: Request,
    principal: Principal = Depends(require_scope("organizations.manage")),
    core: GrpcBankingClient = Depends(get_core),
    session: AsyncSession = Depends(get_session),
) -> TickerAvailability:
    if principal.via_api_key or principal.via_bot:
        raise HTTPException(403, "Проверка тикера доступна только в личном кабинете")
    await lookup_limit(session, principal.user_id)
    return TickerAvailability.model_validate(
        await call(request, core, "read.ticker_availability", {"ticker": ticker}, principal)
    )


@router.get("/exchange/stocks/{ticker}")
@router.get("/exchange/stocks/{ticker}/{part}")
async def market(
    ticker: Ticker,
    request: Request,
    part: Literal["orderbook", "trades", "candles"] | None = None,
    period: Literal["1h", "24h", "7d", "30d", "90d", "all"] = "24h",
    limit: int = Query(25, ge=1, le=100),
    cursor: Annotated[str, Query(pattern=r"^[0-9]{0,19}$")] = "",
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await call(
        request,
        core,
        "read." + (part or "stock"),
        {"ticker": ticker, "period": period, "limit": limit, "cursor": cursor},
    )


@router.post("/exchange/orders", status_code=201)
async def order(
    body: OrderInput,
    request: Request,
    idempotency_key: Annotated[IdempotencyKey, Header()],
    principal: Principal = Depends(require_scope("exchange.trade")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await call(
        request,
        core,
        "order.create",
        {**body.model_dump(mode="json", exclude_none=True), "key": idempotency_key},
        principal,
    )


@router.get("/exchange/orders")
@router.get("/exchange/orders/{order_id}")
async def orders(
    request: Request,
    order_id: uuid.UUID | None = None,
    status: Literal["", "open", "partially_filled", "filled", "cancelled", "expired"] = "",
    limit: int = Query(25, ge=1, le=100),
    cursor: uuid.UUID | None = None,
    principal: Principal = Depends(require_scope("exchange.read")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await call(
        request,
        core,
        "read.order" if order_id else "read.orders",
        {"status": status, "limit": limit, "cursor": str(cursor) if cursor else ""},
        principal,
        str(order_id) if order_id else "",
    )


@router.post("/exchange/orders/{order_id}/cancel")
async def cancel(
    order_id: uuid.UUID,
    body: Confirm,
    request: Request,
    idempotency_key: Annotated[IdempotencyKey, Header()],
    principal: Principal = Depends(require_scope("exchange.trade")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await call(
        request, core, "order.cancel", {"key": idempotency_key, **body.model_dump()}, principal, str(order_id)
    )


@router.get("/exchange/portfolio")
@router.get("/exchange/portfolio/{part}")
async def portfolio(
    account_id: uuid.UUID,
    request: Request,
    part: Literal["history", "dividend_history"] | None = None,
    limit: int = Query(25, ge=1, le=100),
    cursor: Annotated[str, Query(pattern=r"^[a-fA-F0-9-]{0,36}$")] = "",
    principal: Principal = Depends(require_scope("exchange.read")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    if cursor:
        if part == "history":
            if not cursor.isdecimal() or len(cursor) > 19:
                raise HTTPException(422, "Invalid trade cursor")
        else:
            try:
                uuid.UUID(cursor)
            except ValueError as error:
                raise HTTPException(422, "Invalid cursor") from error
    return await call(
        request,
        core,
        "read." + ({None: "portfolio", "history": "history", "dividend_history": "dividend_history"}[part]),
        {"account_id": str(account_id), "limit": limit, "cursor": cursor},
        principal,
    )


@router.post("/exchange/issuers", status_code=201)
async def create_issuer(
    body: IssuerInput,
    request: Request,
    idempotency_key: Annotated[IdempotencyKey, Header()],
    principal: Principal = Depends(require_scope("exchange.trade")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await call(
        request, core, "issuer.create", {**body.model_dump(mode="json"), "key": idempotency_key}, principal
    )


@router.get("/exchange/issuers")
async def issuers(
    organization_id: uuid.UUID,
    request: Request,
    limit: int = Query(25, ge=1, le=100),
    cursor: uuid.UUID | None = None,
    principal: Principal = Depends(require_scope("exchange.read")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await call(
        request,
        core,
        "read.issuers",
        {"limit": limit, "cursor": str(cursor) if cursor else ""},
        principal,
        org=str(organization_id),
    )


@router.get("/exchange/issuers/{issuer_id}")
async def issuer(
    issuer_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_scope("exchange.read")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await call(request, core, "read.issuer", {}, principal, str(issuer_id))


@router.post("/exchange/issuers/{issuer_id}/submit")
async def submit(
    issuer_id: uuid.UUID,
    body: Confirm,
    request: Request,
    idempotency_key: Annotated[IdempotencyKey, Header()],
    principal: Principal = Depends(require_scope("exchange.trade")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await call(
        request, core, "issuer.submit", {"key": idempotency_key, **body.model_dump()}, principal, str(issuer_id)
    )


@router.post("/exchange/issuers/{issuer_id}/ipo")
async def ipo(
    issuer_id: uuid.UUID,
    body: IPOInput,
    request: Request,
    idempotency_key: Annotated[IdempotencyKey, Header()],
    principal: Principal = Depends(require_scope("exchange.trade")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await call(
        request, core, "ipo.create", {**body.model_dump(mode="json"), "key": idempotency_key}, principal, str(issuer_id)
    )


@router.post("/exchange/issuers/{issuer_id}/ipo/buy")
async def ipo_buy(
    issuer_id: uuid.UUID,
    body: IPOBuy,
    request: Request,
    idempotency_key: Annotated[IdempotencyKey, Header()],
    principal: Principal = Depends(require_scope("exchange.trade")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await call(
        request, core, "ipo.buy", {**body.model_dump(mode="json"), "key": idempotency_key}, principal, str(issuer_id)
    )


@router.get("/exchange/dividends")
async def dividends(
    organization_id: uuid.UUID,
    request: Request,
    limit: int = Query(25, ge=1, le=100),
    cursor: uuid.UUID | None = None,
    principal: Principal = Depends(require_scope("exchange.read")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await call(
        request,
        core,
        "read.dividends",
        {"limit": limit, "cursor": str(cursor) if cursor else ""},
        principal,
        org=str(organization_id),
    )


@router.post("/exchange/dividends", status_code=201)
async def create_dividend(
    body: DividendInput,
    request: Request,
    idempotency_key: Annotated[IdempotencyKey, Header()],
    principal: Principal = Depends(require_scope("exchange.trade")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await call(
        request, core, "dividend.create", {**body.model_dump(mode="json"), "key": idempotency_key}, principal
    )


@router.post("/exchange/dividends/{dividend_id}/{action}")
async def dividend_action(
    dividend_id: uuid.UUID,
    action: Literal["approve", "execute"],
    body: Confirm,
    request: Request,
    idempotency_key: Annotated[IdempotencyKey, Header()],
    principal: Principal = Depends(require_scope("exchange.trade")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await call(
        request, core, "dividend." + action, {"key": idempotency_key, **body.model_dump()}, principal, str(dividend_id)
    )


@router.get("/admin/exchange/{part}")
async def admin_exchange(
    part: Literal["issuers", "orders", "trades", "audit", "issuances", "surveillance"],
    request: Request,
    status: str = Query("", max_length=30),
    cursor: Annotated[str, Query(pattern=r"^[a-fA-F0-9-]{0,36}$")] = "",
    limit: int = Query(25, ge=1, le=100),
    principal: Principal = Depends(require_admin("admin.exchange.read")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    if cursor:
        if part in {"orders", "trades"}:
            if not cursor.isdecimal() or len(cursor) > 19:
                raise HTTPException(422, "Invalid cursor")
        else:
            try:
                uuid.UUID(cursor)
            except ValueError as error:
                raise HTTPException(422, "Invalid cursor") from error
    return await call(
        request,
        core,
        "read."
        + {
            "issuers": "admin",
            "orders": "admin_orders",
            "trades": "admin_trades",
            "audit": "audit",
            "issuances": "issuances",
            "surveillance": "surveillance",
        }[part],
        {"status": status, "cursor": cursor, "limit": limit},
        principal,
    )


@router.post("/admin/exchange/issuers/{issuer_id}/moderate")
async def moderate(
    issuer_id: uuid.UUID,
    body: Moderation,
    request: Request,
    idempotency_key: Annotated[IdempotencyKey, Header()],
    principal: Principal = Depends(require_admin("admin.exchange.manage")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await call(
        request, core, "issuer.moderate", {**body.model_dump(), "key": idempotency_key}, principal, str(issuer_id)
    )


@router.post("/admin/exchange/issuers/{issuer_id}/additional")
async def additional(
    issuer_id: uuid.UUID,
    body: Additional,
    request: Request,
    idempotency_key: Annotated[IdempotencyKey, Header()],
    principal: Principal = Depends(require_admin("admin.exchange.manage")),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await call(
        request, core, "issuer.additional", {**body.model_dump(), "key": idempotency_key}, principal, str(issuer_id)
    )

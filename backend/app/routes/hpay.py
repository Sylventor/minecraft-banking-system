"""Organization catalogue and authenticated Minecraft payment commands."""

from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..dependencies import get_core, request_ids
from ..grpc.client import GrpcBankingClient
from ..schemas import PositiveAmount, StrictModel
from ..security import Principal, require_scope
from .atm import Identity, dispatch, linked, service

router = APIRouter(prefix="/api/v1", tags=["HPay"])
catalogue = "/organizations/{org}/hpay"
internal = "/internal/atm/hpay"
ProductId = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{0,18}$")]


class ProductInput(StrictModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    description: str = Field(default="", max_length=500)
    account_id: UUID
    price: PositiveAmount
    status: Literal["active", "disabled", "archived"] = "active"


class Quote(Identity):
    product_id: ProductId


class Confirm(Quote):
    purchase_id: UUID
    protection_seconds: int = Field(default=3, strict=True, ge=1, le=60)


def interactive(principal: Principal = Depends(require_scope("profile.read"))) -> Principal:
    if principal.via_api_key or principal.via_bot:
        raise HTTPException(403, "HPay catalogue requires an interactive session")
    return principal


async def catalog_call(
    core: GrpcBankingClient,
    request: Request,
    principal: Principal,
    org: UUID,
    action: str,
    product: str = "",
    **payload: Any,
) -> dict[str, Any]:
    return await core.business(principal.user_id, str(org), "hpay." + action, product, payload, *request_ids(request))


@router.get(catalogue + "/products")
async def products(
    org: UUID,
    request: Request,
    offset: int = Query(0, ge=0, le=1000000),
    principal: Principal = Depends(interactive),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await catalog_call(core, request, principal, org, "list", offset=offset)


@router.get(catalogue + "/products/{product}")
async def product_detail(
    org: UUID,
    request: Request,
    product: ProductId,
    principal: Principal = Depends(interactive),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    result = await catalog_call(core, request, principal, org, "get", product)
    if not result["items"]:
        raise HTTPException(404, "HPay product not found")
    return cast(dict[str, Any], result["items"][0])


@router.post(catalogue + "/products", status_code=201)
async def create_product(
    org: UUID,
    request: Request,
    body: ProductInput,
    principal: Principal = Depends(interactive),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    result = await catalog_call(core, request, principal, org, "create", **body.model_dump(mode="json"))
    return cast(dict[str, Any], result["items"][0])


@router.put(catalogue + "/products/{product}")
async def update_product(
    org: UUID,
    request: Request,
    body: ProductInput,
    product: ProductId,
    principal: Principal = Depends(interactive),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    result = await catalog_call(core, request, principal, org, "update", product, **body.model_dump(mode="json"))
    return cast(dict[str, Any], result["items"][0])


@router.get(catalogue + "/sales")
async def sales(
    org: UUID,
    request: Request,
    offset: int = Query(0, ge=0, le=1000000),
    principal: Principal = Depends(interactive),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await catalog_call(core, request, principal, org, "sales", offset=offset)


async def player_call(
    action: str,
    body: Identity,
    request: Request,
    session: AsyncSession,
    core: GrpcBankingClient,
) -> dict[str, Any]:
    buyer = await linked(session, body)
    return await dispatch(core, request, "hpay." + action, {**body.model_dump(mode="json"), "user_id": buyer})


@router.post(internal + "/quote", dependencies=[Depends(service)])
async def quote(
    body: Quote,
    request: Request,
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await player_call("quote", body, request, session, core)


@router.post(internal + "/confirm", dependencies=[Depends(service)])
async def confirm(
    body: Confirm,
    request: Request,
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await player_call("confirm", body, request, session, core)


@router.post(internal + "/cancel", dependencies=[Depends(service)])
async def cancel(
    body: Identity,
    request: Request,
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return await player_call("cancel", body, request, session, core)

from __future__ import annotations

from fastapi import HTTPException, Request

from .grpc.client import BankingClientProvider, GrpcBankingClient
from .security import Principal


async def get_core(request: Request) -> GrpcBankingClient:
    provider: BankingClientProvider = request.app.state.core_provider
    return await provider.get()


def request_ids(request: Request) -> tuple[str, str]:
    return request.state.request_id, request.state.correlation_id


def request_source(principal: Principal) -> str:
    return "discord" if principal.via_bot else "integration" if principal.via_api_key else "web"


def require_idempotency(request: Request) -> str:
    value = request.headers.get("idempotency-key", "")
    if (
        not 16 <= len(value) <= 255
        or not value.isascii()
        or not value.replace("-", "").replace("_", "").replace(".", "").replace(":", "").isalnum()
    ):
        raise HTTPException(400, "Требуется корректный Idempotency-Key")
    return value

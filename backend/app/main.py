from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse, RedirectResponse

from .api_documentation import install_api_documentation
from .authentication import finish_discord_link, issue_session
from .authentication import router as authentication_router
from .config import Settings, get_settings
from .database import get_session
from .experience import assign_username, ensure_organization, notify, profile_view
from .grpc.client import BankingClientProvider, CoreError, GrpcBankingClient
from .localization import MESSAGES, error_message
from .logging import configure_logging
from .middleware import RequestGuards
from .models import (
    ApiKey,
    AuditEvent,
    ExternalIdentity,
    OAuthState,
    Organization,
    OrganizationMember,
    User,
    UserSession,
)
from .notifications import relay_loop, stop_task
from .routes.admin_finance import router as admin_finance_router
from .routes.administration import router as administration_router
from .routes.atm import router as atm_router
from .routes.banking import router as banking_router
from .routes.business import router as business_router
from .routes.exchange import router as exchange_router
from .routes.hpay import router as hpay_router
from .routes.minecraft import router as minecraft_router
from .routes.minecraft_atm import router as minecraft_atm_router
from .routes.news import router as news_router
from .routes.notification_delivery import router as notification_delivery_router
from .routes.payment_requests import router as payment_requests_router
from .routes.privacy import router as privacy_router
from .routes.roles import router as roles_router
from .routes.sessions import router as sessions_router
from .routes.support import router as support_router
from .routes.terms import router as terms_router
from .schemas import (
    AccountCreateRequest,
    AccountsPage,
    AccountView,
    ApiKeyCreated,
    ApiKeyCreateRequest,
    ApiKeyView,
    HoldActionRequest,
    HoldRequest,
    IssueRequest,
    OperationPage,
    OperationView,
    OrganizationCreate,
    RefundRequest,
    TransferRequest,
)
from .security import (
    VALID_SCOPES,
    Principal,
    current_principal,
    new_api_key,
    require_admin,
    require_resource,
    require_scope,
    secret_hash,
)

log = structlog.get_logger()


def utcnow() -> datetime:
    return datetime.now(UTC)


def request_ids(request: Request) -> tuple[str, str]:
    return request.state.request_id, request.state.correlation_id


def request_source(principal: Principal) -> str:
    return "discord" if principal.via_bot else "integration" if principal.via_api_key else "web"


def error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": error_message(code, message, request.headers.get("accept-language", "ru")),
            "code": code,
            "request_id": getattr(request.state, "request_id", "unknown"),
        },
    )


async def get_core(request: Request) -> GrpcBankingClient:
    provider: BankingClientProvider = request.app.state.core_provider
    return await provider.get()


def require_idempotency(request: Request) -> str:
    value = request.headers.get("idempotency-key", "")
    if (
        not (16 <= len(value) <= 255)
        or not value.replace("-", "").replace("_", "").replace(".", "").replace(":", "").isalnum()
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "valid Idempotency-Key is required")
    return value


async def audit(
    session: AsyncSession,
    actor: str | None,
    action: str,
    target_type: str,
    target_id: str,
    metadata: dict[str, object] | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor_id=uuid.UUID(actor) if actor else None,
            action=action,
            target_type=target_type,
            target_id=target_id,
            metadata_json=metadata or {},
        )
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(relay_loop(application.state.core_provider))
        try:
            yield
        finally:
            await stop_task(task)
            if application.state.core_provider.client:
                await application.state.core_provider.client.close()

    app = FastAPI(
        title=settings.project_name + " API",
        version="2.6.1",
        openapi_url="/api/v1/openapi.json",
        docs_url="/docs",
        lifespan=lifespan,
    )
    app.state.core_provider = BankingClientProvider(settings)
    app.state.oauth_client_factory = lambda: httpx.AsyncClient(timeout=5)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "PATCH", "PUT"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Correlation-ID", "X-Request-ID"],
    )
    app.add_middleware(RequestGuards, settings=settings)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, _error: Exception) -> JSONResponse:
        log.error("unhandled_error", request_id=getattr(request.state, "request_id", "unknown"), service="api")
        return error_response(request, 500, "internal_error", "internal server error")

    @app.exception_handler(IntegrityError)
    async def constraint_error(request: Request, _error: IntegrityError) -> JSONResponse:
        return error_response(request, 409, "constraint_conflict", "the request conflicts with existing data")

    @app.exception_handler(httpx.HTTPError)
    async def provider_error(request: Request, _error: httpx.HTTPError) -> JSONResponse:
        return error_response(request, 503, "oauth_provider_unavailable", "Discord OAuth is temporarily unavailable")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        response = error_response(request, error.status_code, "request_rejected", str(error.detail))
        if error.headers:
            response.headers.update(error.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _error: RequestValidationError) -> JSONResponse:
        return error_response(request, 422, "validation_error", "request data is invalid")

    @app.exception_handler(CoreError)
    async def core_error(request: Request, error: CoreError) -> JSONResponse:
        mapping = {
            "PERMISSION_DENIED": 403,
            "UNAUTHENTICATED": 502,
            "INVALID_ARGUMENT": 422,
            "ALREADY_EXISTS": 409,
            "FAILED_PRECONDITION": 409,
            "RESOURCE_EXHAUSTED": 422,
            "NOT_FOUND": 404,
            "UNAVAILABLE": 503,
            "DEADLINE_EXCEEDED": 504,
        }
        prefix = "exchange_" if "/exchange/" in request.url.path and str(error) in MESSAGES else "core_"
        return error_response(request, mapping.get(error.code, 502), prefix + error.code.lower(), str(error))

    @app.get("/healthz", tags=["operations"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["operations"])
    async def readiness(
        session: AsyncSession = Depends(get_session), core: GrpcBankingClient = Depends(get_core)
    ) -> dict[str, str]:
        try:
            revision = await session.scalar(text("SELECT version_num FROM auth.alembic_version"))
        except SQLAlchemyError as error:
            raise HTTPException(503, "authentication database is not ready") from error
        if revision != "auth_0013":
            raise HTTPException(503, "authentication schema migration is required")
        try:
            await asyncio.wait_for(core.ready(), timeout=1)
        except (TimeoutError, CoreError) as error:
            raise HTTPException(503, "financial core is not ready") from error
        return {"status": "ready"}

    @app.get("/api/v1/auth/discord/login", tags=["auth"])
    async def discord_login(response: Response, age_confirmed: bool = False, session: AsyncSession = Depends(get_session)) -> Response:
        if not settings.discord_client_id or not settings.discord_redirect_uri:
            raise HTTPException(503, "Discord OAuth is not configured")
        state = secrets.token_urlsafe(32)
        state_hash = hashlib.sha256(state.encode()).hexdigest()
        session.add(
            OAuthState(
                state_hash=state_hash,
                age_confirmed=age_confirmed,
                redirect_uri=settings.discord_redirect_uri,
                expires_at=utcnow() + timedelta(seconds=settings.oauth_state_ttl_seconds),
            )
        )
        await session.commit()
        query = urlencode(
            {
                "client_id": settings.discord_client_id,
                "redirect_uri": settings.discord_redirect_uri,
                "response_type": "code",
                "scope": "identify",
                "state": state,
            }
        )
        redirect = RedirectResponse("https://discord.com/api/oauth2/authorize?" + query, status_code=302)
        redirect.set_cookie(
            "horizon_oauth_state",
            state,
            httponly=True,
            secure=settings.production,
            samesite="lax",
            max_age=settings.oauth_state_ttl_seconds,
        )
        return redirect

    @app.get("/api/v1/auth/discord/callback", tags=["auth"])
    async def discord_callback(
        request: Request, code: str, state: str, session: AsyncSession = Depends(get_session)
    ) -> Response:
        if not hmac_compare(state, request.cookies.get("horizon_oauth_state", "")):
            raise HTTPException(400, "OAuth state mismatch")
        record = await session.scalar(
            select(OAuthState)
            .where(OAuthState.state_hash == hashlib.sha256(state.encode()).hexdigest())
            .with_for_update()
        )
        if record is None or record.consumed_at is not None or record.expires_at <= utcnow():
            raise HTTPException(400, "OAuth state is expired")
        async with request.app.state.oauth_client_factory() as client:
            token = await client.post(
                "https://discord.com/api/oauth2/token",
                data={
                    "client_id": settings.discord_client_id,
                    "client_secret": settings.discord_client_secret.get_secret_value(),
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.discord_redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            token.raise_for_status()
            identity = await client.get(
                "https://discord.com/api/users/@me", headers={"Authorization": "Bearer " + token.json()["access_token"]}
            )
            identity.raise_for_status()
        profile = identity.json()
        external_id = str(profile["id"])
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity,0))"), {"identity": "discord:" + external_id}
        )
        if record.purpose in {"link", "reauth"}:
            return await finish_discord_link(request, session, record, profile, settings)
        existing = await session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.provider == "discord", ExternalIdentity.external_id == external_id
            )
        )
        if existing is None:
            if not record.age_confirmed:
                record.consumed_at = utcnow()
                await session.commit()
                redirect = RedirectResponse(str(settings.public_origin).rstrip("/") + "/register?age=required", status_code=303)
                redirect.delete_cookie("horizon_oauth_state", path="/")
                return redirect
            user = User(display_name=str(profile.get("global_name") or profile.get("username") or ""), age_confirmed_at=utcnow())
            session.add(user)
            await session.flush()
            await assign_username(session, user)
            session.add(
                ExternalIdentity(
                    user_id=user.id,
                    provider="discord",
                    external_id=external_id,
                    username=str(profile.get("username", "")),
                )
            )
        else:
            existing_user = await session.get(User, existing.user_id)
            if existing_user is None:
                raise HTTPException(401, "profile is unavailable")
            user = existing_user
        if user.status != "active" or user.processing_restricted_at is not None or user.erased_at is not None:
            raise HTTPException(403, "profile is not active")
        if not user.bank_username:
            await assign_username(session, user)
        if existing is not None:
            existing.username = str(profile.get("username", existing.username))[:100]
        # Persist identity provisioning before crossing the service boundary. A failed
        # core call is resumed on the next login; no usable session is issued early.
        record.consumed_at = utcnow()
        await session.commit()
        core = await request.app.state.core_provider.get()
        await core.primary_account(str(user.id), *request_ids(request))
        ready_user = await session.scalar(
            select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True)
        )
        if ready_user is None or ready_user.status != "active" or ready_user.processing_restricted_at is not None or ready_user.erased_at is not None:
            raise HTTPException(403, "profile is not active")
        user = ready_user
        user.banking_ready = True
        await audit(session, str(user.id), "auth.discord_login", "user", str(user.id))
        await notify(
            session,
            str(user.id),
            "login:" + str(uuid.uuid4()),
            "security",
            "Выполнен вход в банк",
            "Новая сессия создана через подтверждённый Discord-аккаунт.",
        )
        redirect = RedirectResponse(str(settings.public_origin), status_code=302)
        redirect.delete_cookie("horizon_oauth_state")
        await issue_session(session, user, request, redirect, settings, "discord")
        return redirect

    @app.post("/api/v1/auth/logout", status_code=204, tags=["auth"])
    async def logout(request: Request, session: AsyncSession = Depends(get_session)) -> Response:
        token = request.cookies.get("horizon_session", "")
        record = await session.get(UserSession, hashlib.sha256(token.encode()).hexdigest()) if token else None
        if record:
            record.revoked_at = utcnow()
            await session.commit()
        response = Response(status_code=204)
        response.delete_cookie("horizon_session")
        return response

    @app.get("/api/v1/me", tags=["auth"])
    async def me(
        principal: Principal = Depends(require_scope("profile.read")), session: AsyncSession = Depends(get_session)
    ) -> dict[str, object]:
        return await profile_view(session, principal)

    @app.get("/api/v1/accounts", response_model=AccountsPage, tags=["accounts"])
    async def accounts(
        request: Request,
        limit: int = Query(50, ge=1, le=100),
        cursor: uuid.UUID | None = None,
        principal: Principal = Depends(require_scope("accounts.read")),
        core: GrpcBankingClient = Depends(get_core),
    ) -> dict[str, Any]:
        rid, cid = request_ids(request)
        result = await core.accounts(principal.user_id, limit, str(cursor) if cursor else "", rid, cid)
        if principal.via_api_key:
            result["items"] = [row for row in result["items"] if row["id"] in (principal.account_ids or ())]
        return result

    @app.get("/api/v1/accounts/{account_id}/balance", response_model=AccountView, tags=["accounts"])
    async def account_balance(
        account_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(require_scope("balance.read")),
        core: GrpcBankingClient = Depends(get_core),
    ) -> dict[str, Any]:
        require_resource(principal, str(account_id))
        rid, cid = request_ids(request)
        return await core.balance(principal.user_id, str(account_id), rid, cid)

    @app.post("/api/v1/accounts", status_code=201, response_model=AccountView, tags=["accounts"])
    async def create_account(
        body: AccountCreateRequest,
        request: Request,
        principal: Principal = Depends(require_scope("accounts.create")),
        core: GrpcBankingClient = Depends(get_core),
    ) -> dict[str, Any]:
        if principal.via_api_key:
            raise HTTPException(403, "account creation requires an interactive session")
        rid, cid = request_ids(request)
        return await core.create_account(
            principal.user_id,
            body.account_type,
            rid,
            cid,
            body.name,
            require_idempotency(request) if request.headers.get("idempotency-key") else "",
        )

    @app.get("/api/v1/accounts/{account_id}/operations", response_model=OperationPage, tags=["accounts"])
    async def account_operations(
        account_id: uuid.UUID,
        request: Request,
        limit: int = Query(50, ge=1, le=100),
        cursor: uuid.UUID | None = None,
        principal: Principal = Depends(require_scope("history.read")),
        core: GrpcBankingClient = Depends(get_core),
    ) -> dict[str, Any]:
        require_resource(principal, str(account_id))
        rid, cid = request_ids(request)
        return await core.history(principal.user_id, str(account_id), limit, rid, cid, str(cursor) if cursor else "")

    @app.get("/api/v1/operations/{operation_id}", response_model=OperationView, tags=["accounts"])
    async def operation(
        operation_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(require_scope("history.read")),
        core: GrpcBankingClient = Depends(get_core),
    ) -> dict[str, Any]:
        rid, cid = request_ids(request)
        result = await core.operation(principal.user_id, str(operation_id), rid, cid)
        if principal.via_api_key:
            if not any(
                account in (principal.account_ids or ())
                for account in (result["source_account_id"], result["destination_account_id"])
            ):
                raise HTTPException(403, "API key does not grant access to this operation")
        return result

    async def run_money(
        method: str, payload: dict[str, Any], principal: Principal, core: GrpcBankingClient, request: Request
    ) -> dict[str, Any]:
        require_resource(principal, str(payload.get("source_account_id", payload.get("target_account_id", ""))))
        rid, cid = request_ids(request)
        return await core.money_operation(
            method, principal.user_id, payload, require_idempotency(request), rid, cid, request_source(principal)
        )

    @app.post("/api/v1/transfers", status_code=201, response_model=OperationView, tags=["money"])
    async def transfer(
        body: TransferRequest,
        request: Request,
        principal: Principal = Depends(require_scope("transfers.create")),
        core: GrpcBankingClient = Depends(get_core),
    ) -> dict[str, Any]:
        return await run_money("Transfer", body.model_dump(mode="json"), principal, core, request)

    @app.post("/api/v1/payments", status_code=201, response_model=OperationView, tags=["money"])
    async def payment(
        body: TransferRequest,
        request: Request,
        principal: Principal = Depends(require_scope("payments.create")),
        core: GrpcBankingClient = Depends(get_core),
    ) -> dict[str, Any]:
        return await run_money("CreatePayment", body.model_dump(mode="json"), principal, core, request)

    @app.post("/api/v1/holds", status_code=201, response_model=OperationView, tags=["money"])
    async def hold(
        body: HoldRequest,
        request: Request,
        principal: Principal = Depends(require_scope("holds.create")),
        core: GrpcBankingClient = Depends(get_core),
    ) -> dict[str, Any]:
        require_resource(principal, str(body.account_id))
        rid, cid = request_ids(request)
        return await core.hold(
            principal.user_id,
            body.model_dump(mode="json"),
            require_idempotency(request),
            rid,
            cid,
            request_source(principal),
        )

    @app.post("/api/v1/holds/{hold_id}/release", tags=["money"])
    async def release_hold(
        hold_id: uuid.UUID,
        body: HoldActionRequest,
        request: Request,
        principal: Principal = Depends(require_scope("holds.create")),
        core: GrpcBankingClient = Depends(get_core),
    ) -> dict[str, Any]:
        if principal.via_api_key:
            raise HTTPException(403, "hold actions require interactive authentication")
        rid, cid = request_ids(request)
        return await core.hold_action(
            "ReleaseHold",
            principal.user_id,
            str(hold_id),
            body.model_dump(mode="json"),
            require_idempotency(request),
            rid,
            cid,
            request_source(principal),
        )

    @app.post("/api/v1/holds/{hold_id}/capture", tags=["money"])
    async def capture_hold(
        hold_id: uuid.UUID,
        body: HoldActionRequest,
        request: Request,
        principal: Principal = Depends(require_scope("holds.create")),
        core: GrpcBankingClient = Depends(get_core),
    ) -> dict[str, Any]:
        if principal.via_api_key:
            raise HTTPException(403, "hold actions require interactive authentication")
        if body.destination_account_id is None:
            raise HTTPException(422, "capture destination is required")
        rid, cid = request_ids(request)
        return await core.hold_action(
            "CaptureHold",
            principal.user_id,
            str(hold_id),
            body.model_dump(mode="json"),
            require_idempotency(request),
            rid,
            cid,
            request_source(principal),
        )

    @app.post("/api/v1/admin/refunds", status_code=201, tags=["admin"])
    async def refund(
        body: RefundRequest,
        request: Request,
        principal: Principal = Depends(require_admin("admin.refunds")),
        core: GrpcBankingClient = Depends(get_core),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        result = await run_money("Refund", body.model_dump(mode="json"), principal, core, request)
        await audit(
            session,
            principal.user_id,
            "money.refund",
            "operation",
            result["id"],
            {"original_operation_id": str(body.original_operation_id)},
        )
        await session.commit()
        return result

    @app.post("/api/v1/admin/issuances", status_code=201, tags=["admin"])
    async def issue(
        body: IssueRequest,
        request: Request,
        principal: Principal = Depends(require_admin("admin.issuance")),
        core: GrpcBankingClient = Depends(get_core),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        result = await run_money("Issue", body.model_dump(mode="json"), principal, core, request)
        await audit(
            session,
            principal.user_id,
            "money.issue",
            "operation",
            result["id"],
            {"authority_reference": body.authority_reference},
        )
        await session.commit()
        return result

    @app.post("/api/v1/api-keys", response_model=ApiKeyCreated, status_code=201, tags=["api keys"])
    async def create_api_key(
        body: ApiKeyCreateRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
        session: AsyncSession = Depends(get_session),
        core: GrpcBankingClient = Depends(get_core),
    ) -> ApiKeyCreated:
        if principal.via_api_key or principal.via_bot:
            raise HTTPException(403, "API key creation requires an interactive session")
        unknown = set(body.scopes) - VALID_SCOPES
        if unknown:
            raise HTTPException(422, "unknown API key scope")
        merchant_scopes = {
            "payment_requests.create",
            "payment_requests.read",
            "payment_requests.cancel",
            "recipients.read",
        }
        if body.organization_id:
            if not body.account_ids or not set(body.scopes) <= merchant_scopes:
                raise HTTPException(422, "Merchant keys require accounts and payment-request-only scopes")
            org = await core.organization(principal.user_id, str(body.organization_id), *request_ids(request))
            if org["current_role"] not in {"owner", "administrator"}:
                raise HTTPException(403, "Only organization managers can create merchant keys")
            eligible = {a["id"] for a in org["accounts"] if "payment_requests.create" in a["permissions"]}
            if not {str(a) for a in body.account_ids} <= eligible:
                raise HTTPException(403, "Merchant account does not belong to the selected organization")
        elif any(s.startswith("payment_requests.") for s in body.scopes):
            raise HTTPException(422, "Payment-request API keys must be organization-bound")
        prefix, secret, full = new_api_key(settings)
        key = ApiKey(
            id=uuid.uuid4(),
            owner_id=uuid.UUID(principal.user_id),
            prefix=prefix,
            secret_hash=secret_hash(secret, settings),
            name=body.name,
            scopes=body.scopes,
            account_ids=body.account_ids,
            organization_id=body.organization_id,
        )
        session.add(key)
        await audit(session, principal.user_id, "api_key.create", "api_key", str(key.id), {"scopes": body.scopes})
        await notify(
            session,
            principal.user_id,
            "api-key:create:" + str(key.id),
            "security",
            "Создан API-ключ",
            "В вашем профиле создан новый ключ доступа. Если это были не вы, отзовите ключ в кабинете.",
        )
        await session.commit()
        await session.refresh(key)
        return ApiKeyCreated(
            id=key.id, prefix=key.prefix, secret=full, name=key.name, scopes=key.scopes, created_at=key.created_at
        )

    @app.get("/api/v1/api-keys", response_model=list[ApiKeyView], tags=["api keys"])
    async def list_api_keys(
        principal: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)
    ) -> list[ApiKeyView]:
        if principal.via_api_key or principal.via_bot:
            raise HTTPException(403, "API key management requires an interactive session")
        rows = (
            await session.scalars(
                select(ApiKey).where(ApiKey.owner_id == uuid.UUID(principal.user_id)).order_by(ApiKey.created_at.desc())
            )
        ).all()
        return [
            ApiKeyView(
                id=k.id,
                prefix=k.prefix,
                name=k.name,
                scopes=k.scopes,
                created_at=k.created_at,
                revoked_at=k.revoked_at,
                account_ids=k.account_ids,
                organization_id=k.organization_id,
            )
            for k in rows
        ]

    @app.delete("/api/v1/api-keys/{key_id}", status_code=204, tags=["api keys"])
    async def revoke_api_key(
        key_id: uuid.UUID,
        principal: Principal = Depends(current_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        if principal.via_api_key or principal.via_bot:
            raise HTTPException(403, "API key management requires an interactive session")
        key = await session.scalar(
            select(ApiKey).where(ApiKey.id == key_id, ApiKey.owner_id == uuid.UUID(principal.user_id)).with_for_update()
        )
        if key is None:
            raise HTTPException(404, "API key not found")
        key.revoked_at = utcnow()
        await audit(session, principal.user_id, "api_key.revoke", "api_key", str(key_id))
        await notify(
            session,
            principal.user_id,
            "api-key:revoke:" + str(key_id),
            "security",
            "API-ключ отозван",
            "Один из ваших ключей доступа больше не действует.",
        )
        await session.commit()
        return Response(status_code=204)

    @app.post("/api/v1/organizations", status_code=201, tags=["organizations"])
    async def create_organization(
        body: OrganizationCreate,
        request: Request,
        principal: Principal = Depends(require_scope("organizations.manage")),
        session: AsyncSession = Depends(get_session),
        core: GrpcBankingClient = Depends(get_core),
    ) -> dict[str, Any]:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": "organization-name:" + body.name.casefold()},
        )
        organization = await session.scalar(select(Organization).where(Organization.name == body.name))
        if organization is not None and str(organization.owner_id) != principal.user_id:
            raise HTTPException(409, "Название организации уже занято")
        if organization is None:
            organization = Organization(name=body.name, owner_id=uuid.UUID(principal.user_id))
            session.add(organization)
            await session.flush()
            session.add(
                OrganizationMember(organization_id=organization.id, user_id=organization.owner_id, role="owner")
            )
            await audit(session, principal.user_id, "organization.create", "organization", str(organization.id))
        await session.commit()
        return await ensure_organization(session, core, principal.user_id, organization.id, *request_ids(request))

    @app.get("/api/v1/organizations", tags=["organizations"])
    async def list_organizations(
        principal: Principal = Depends(require_scope("organizations.manage")),
        session: AsyncSession = Depends(get_session),
    ) -> list[dict[str, str]]:
        rows = (
            (
                await session.execute(
                    select(Organization)
                    .join(OrganizationMember)
                    .where(OrganizationMember.user_id == uuid.UUID(principal.user_id))
                )
            )
            .scalars()
            .all()
        )
        return [{"id": str(row.id), "name": row.name} for row in rows]

    app.include_router(authentication_router)
    app.include_router(administration_router)
    app.include_router(roles_router)
    app.include_router(terms_router)
    app.include_router(news_router)
    app.include_router(payment_requests_router)
    app.include_router(business_router)
    app.include_router(sessions_router)
    app.include_router(exchange_router)
    app.include_router(notification_delivery_router)
    app.include_router(admin_finance_router)
    app.include_router(minecraft_router)
    app.include_router(minecraft_atm_router)
    app.include_router(atm_router)
    app.include_router(hpay_router)
    app.include_router(privacy_router)
    app.include_router(support_router)
    app.include_router(banking_router)
    install_api_documentation(app, str(settings.public_origin))
    return app


def hmac_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left.encode(), right.encode())


app = create_app()

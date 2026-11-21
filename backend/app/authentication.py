"""Interactive authentication shares the existing opaque PostgreSQL sessions."""

import asyncio
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import Field, SecretStr, field_validator
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from .config import Settings, get_settings
from .database import get_session
from .dependencies import get_core, request_ids
from .experience import NAME_PATTERN, RESERVED_NAMES, assign_username, audit, notify
from .grpc.client import GrpcBankingClient
from .models import AuditEvent, ExternalIdentity, OAuthState, PasswordCredential, User, UserSession
from .schemas import StrictModel
from .security import Principal, current_principal

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, type=Type.ID)
# Unknown logins perform the same expensive verification as existing credentials.
dummy_hash = hasher.hash(secrets.token_urlsafe(32))
hash_slots = asyncio.Semaphore(4)


def moment() -> datetime:
    return datetime.now(UTC)


def normalized_login(value: str) -> str:
    candidate = value.strip().lower()
    if not NAME_PATTERN.fullmatch(candidate) or candidate in RESERVED_NAMES:
        raise HTTPException(
            422, "Логин: 3–24 латинских символа, цифры или _, начинается с буквы. Служебные имена запрещены."
        )
    return candidate


class LoginInput(StrictModel):
    login: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=1024)


class NewPassword(StrictModel):
    password: SecretStr = Field(min_length=12, max_length=1024)
    password_confirmation: SecretStr = Field(min_length=12, max_length=1024)

    @field_validator("password_confirmation")
    @classmethod
    def matching(cls, value: SecretStr, info: Any) -> SecretStr:
        original = info.data.get("password")
        if original is not None and value.get_secret_value() != original.get_secret_value():
            raise ValueError("Passwords must match")
        return value


class SetupPassword(NewPassword):
    login: str = Field(min_length=3, max_length=24)


class RegisterInput(SetupPassword):
    age_confirmed: bool = Field(strict=True)

    @field_validator("age_confirmed")
    @classmethod
    def minimum_age(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Confirm that you are at least 15 years old")
        return value


class ChangePassword(NewPassword):
    current_password: SecretStr | None = Field(default=None, min_length=1, max_length=1024)


class PasswordProof(StrictModel):
    password: SecretStr = Field(min_length=1, max_length=1024)


async def rate_limit(session: AsyncSession, subject: str, limit: int, seconds: int = 60) -> None:
    key = hashlib.sha256(subject.encode()).hexdigest()
    attempts = await session.scalar(
        text("""
      INSERT INTO auth.authentication_limits(bucket_hash,window_start,attempts) VALUES(:key,clock_timestamp(),1)
      ON CONFLICT(bucket_hash) DO UPDATE SET
      attempts=CASE WHEN authentication_limits.window_start < clock_timestamp()-make_interval(secs=>:seconds)
        THEN 1 ELSE authentication_limits.attempts+1 END,
      window_start=CASE WHEN authentication_limits.window_start < clock_timestamp()-make_interval(secs=>:seconds)
        THEN clock_timestamp() ELSE authentication_limits.window_start END RETURNING attempts
    """),
        {"key": key, "seconds": seconds},
    )
    await session.execute(
        text(
            "DELETE FROM auth.authentication_limits WHERE bucket_hash IN "
            "(SELECT bucket_hash FROM auth.authentication_limits WHERE window_start<now()-interval '2 days' LIMIT 100)"
        )
    )
    await session.commit()  # Failed authentication must also consume the budget across workers/restarts.
    if attempts is not None and attempts > limit:
        raise HTTPException(429, "Слишком много попыток. Попробуйте позже.", headers={"Retry-After": str(seconds)})


def ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def interactive(principal: Principal) -> None:
    if principal.via_api_key or principal.via_bot:
        raise HTTPException(403, "Управление входом доступно только в личном кабинете")


def recent(request: Request, discord: bool = False) -> None:
    if (moment() - request.state.authenticated_at).total_seconds() > 900 or (
        discord and request.state.auth_method != "discord"
    ):
        raise HTTPException(403, "Повторно подтвердите вход" + (" через Discord" if discord else ""))


async def hash_password(password: SecretStr) -> str:
    async with hash_slots:
        return await asyncio.to_thread(hasher.hash, password.get_secret_value())


async def verify_password(encoded: str, password: SecretStr) -> bool:
    async with hash_slots:
        try:
            return await asyncio.to_thread(hasher.verify, encoded, password.get_secret_value())
        except (VerificationError, InvalidHashError):
            return False


async def locked_user(session: AsyncSession, user_id: str, detail: str = "Профиль недоступен") -> User:
    user = await session.scalar(
        select(User).where(User.id == uuid.UUID(user_id)).with_for_update().execution_options(populate_existing=True)
    )
    if user is None or user.status != "active" or user.processing_restricted_at is not None or user.erased_at is not None:
        raise HTTPException(401, detail)
    return user


async def issue_session(
    session: AsyncSession,
    user: User,
    request: Request,
    response: Response,
    settings: Settings,
    method: str,
    revoke_others: bool = False,
) -> None:
    old = request.cookies.get("horizon_session", "")
    if revoke_others:
        await session.execute(
            update(UserSession)
            .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
            .values(revoked_at=moment())
        )
    elif old:
        record = await session.get(UserSession, hashlib.sha256(old.encode()).hexdigest())
        if record:
            record.revoked_at = moment()
    token = secrets.token_urlsafe(48)
    ttl = min(settings.session_ttl_seconds, settings.session_absolute_ttl_seconds)
    session.add(
        UserSession(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            user_id=user.id,
            expires_at=moment() + timedelta(seconds=ttl),
            absolute_expires_at=moment() + timedelta(seconds=settings.session_absolute_ttl_seconds),
            auth_method=method,
            client_info=request.headers.get("user-agent", "")[:180],
        )
    )
    await session.commit()
    response.set_cookie(
        "horizon_session", token, httponly=True, secure=settings.production, samesite="lax", path="/", max_age=ttl
    )
    response.headers["Cache-Control"] = "no-store"


async def available(session: AsyncSession, login: str, user_id: uuid.UUID | None = None) -> bool:
    credential = await session.scalar(select(PasswordCredential.user_id).where(PasswordCredential.login == login))
    name_owner = await session.scalar(select(User.id).where(func.lower(User.bank_username) == login))
    return credential is None and (name_owner is None or name_owner == user_id)


@router.get("/login-availability")
async def login_availability(
    request: Request, login: str = Query(min_length=3, max_length=24), session: AsyncSession = Depends(get_session)
) -> dict[str, bool]:
    await rate_limit(session, "availability:" + ip(request), 60)
    return {"available": await available(session, normalized_login(login))}


@router.post("/register", status_code=201)
async def register(
    body: RegisterInput,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, str]:
    await rate_limit(session, "register:" + ip(request), 5, 3600)
    login = normalized_login(body.login)
    # Registration and credential setup serialize the same login namespace.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": "login:" + login})
    if not await available(session, login):
        raise HTTPException(409, "Этот логин недоступен")
    user = User(display_name=body.login.strip(), age_confirmed_at=moment())
    session.add(user)
    await session.flush()
    await assign_username(session, user, login)
    session.add(PasswordCredential(user_id=user.id, login=login, password_hash=await hash_password(body.password)))
    audit(session, str(user.id), "auth.password_register", str(user.id))
    await session.commit()
    # A core outage leaves a recoverable existing login, never a second profile.
    await core.primary_account(str(user.id), *request_ids(request))
    user = await locked_user(session, str(user.id))
    user.banking_ready = True
    await issue_session(session, user, request, response, settings, "password")
    return {"id": str(user.id)}


@router.post("/login")
async def login(
    body: LoginInput,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, str]:
    await rate_limit(session, "login-ip:" + ip(request), 30)
    name = body.login.strip().lower()
    await rate_limit(session, "login-name:" + name, 10)
    credential = await session.scalar(select(PasswordCredential).where(PasswordCredential.login == name))
    encoded = credential.password_hash if credential else dummy_hash
    valid = await verify_password(encoded, body.password)
    user = (
        await session.scalar(select(User).where(User.id == credential.user_id).with_for_update())
        if credential and valid
        else None
    )
    if user is None or user.status != "active" or user.processing_restricted_at is not None or user.erased_at is not None:
        session.add(
            AuditEvent(
                action="auth.password_login_failed", target_type="authentication", target_id="", metadata_json={}
            )
        )
        await session.commit()
        raise HTTPException(401, "Неверный логин или пароль")
    # Serialize against a password change that happened during Argon2 verification.
    assert credential is not None
    await session.refresh(credential)
    if credential.password_hash != encoded:
        raise HTTPException(401, "Неверный логин или пароль")
    if hasher.check_needs_rehash(encoded):
        credential.password_hash = await hash_password(body.password)
    verified_hash = credential.password_hash
    await session.commit()
    await core.primary_account(str(user.id), *request_ids(request))
    user = await locked_user(session, str(user.id), "Неверный логин или пароль")
    await session.refresh(credential)
    if credential.password_hash != verified_hash:
        raise HTTPException(401, "Неверный логин или пароль")
    user.banking_ready = True
    audit(session, str(user.id), "auth.password_login", str(user.id))
    await notify(
        session,
        str(user.id),
        "login:" + str(uuid.uuid4()),
        "security",
        "Выполнен вход в банк",
        "Новая сессия создана по логину и паролю.",
    )
    await issue_session(session, user, request, response, settings, "password")
    return {"id": str(user.id)}


@router.get("/methods")
async def methods(
    request: Request, principal: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    interactive(principal)
    credential = await session.get(PasswordCredential, uuid.UUID(principal.user_id))
    discord = await session.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.user_id == uuid.UUID(principal.user_id), ExternalIdentity.provider == "discord"
        )
    )
    return {
        "login": credential.login if credential else None,
        "password_enabled": credential is not None,
        "discord": {"username": discord.username} if discord else None,
        "recent": (moment() - request.state.authenticated_at).total_seconds() <= 900,
        "auth_method": request.state.auth_method,
    }


@router.post("/password/setup", status_code=204)
async def setup(
    body: SetupPassword,
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    interactive(principal)
    recent(request, discord=True)
    await rate_limit(session, "credential:" + principal.user_id, 10)
    user = await locked_user(session, principal.user_id)
    if await session.get(PasswordCredential, user.id):
        raise HTTPException(409, "Вход по паролю уже настроен")
    name = normalized_login(body.login)
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": "login:" + name})
    if not await available(session, name, user.id):
        raise HTTPException(409, "Этот логин недоступен")
    session.add(PasswordCredential(user_id=user.id, login=name, password_hash=await hash_password(body.password)))
    audit(session, principal.user_id, "auth.password_setup", principal.user_id)
    response = Response(status_code=204)
    await issue_session(session, user, request, response, settings, "password", revoke_others=True)
    return response


@router.post("/password/change", status_code=204)
async def change(
    body: ChangePassword,
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    interactive(principal)
    await rate_limit(session, "credential:" + principal.user_id, 10)
    user = await locked_user(session, principal.user_id)
    credential = await session.get(PasswordCredential, user.id)
    if credential is None:
        raise HTTPException(409, "Сначала добавьте вход по паролю")
    if body.current_password is not None:
        if not await verify_password(credential.password_hash, body.current_password):
            raise HTTPException(401, "Неверный текущий пароль")
    else:
        recent(request, discord=True)
    credential.password_hash = await hash_password(body.password)
    credential.updated_at = moment()
    audit(session, principal.user_id, "auth.password_change", principal.user_id)
    response = Response(status_code=204)
    await issue_session(session, user, request, response, settings, "password", revoke_others=True)
    return response


@router.post("/reauth", status_code=204)
async def reauth(
    body: PasswordProof,
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    interactive(principal)
    await rate_limit(session, "credential:" + principal.user_id, 10)
    user = await locked_user(session, principal.user_id)
    credential = await session.get(PasswordCredential, user.id)
    if (
        not await verify_password(credential.password_hash if credential else dummy_hash, body.password)
        or not credential
    ):
        raise HTTPException(401, "Неверный логин или пароль")
    audit(session, principal.user_id, "auth.password_reauth", principal.user_id)
    response = Response(status_code=204)
    await issue_session(session, user, request, response, settings, "password")
    return response


@router.post("/discord/link")
@router.post("/discord/reauth")
async def discord_link(
    request: Request,
    response: Response,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    interactive(principal)
    reauthenticating = request.url.path.endswith("/reauth")
    if not reauthenticating:
        recent(request)
    await rate_limit(session, "discord-link:" + principal.user_id, 5)
    if not settings.discord_client_id or not settings.discord_redirect_uri:
        raise HTTPException(503, "Discord OAuth не настроен")
    state = secrets.token_urlsafe(32)
    session.add(
        OAuthState(
            state_hash=hashlib.sha256(state.encode()).hexdigest(),
            redirect_uri=settings.discord_redirect_uri,
            expires_at=moment() + timedelta(seconds=settings.oauth_state_ttl_seconds),
            purpose="reauth" if reauthenticating else "link",
            user_id=uuid.UUID(principal.user_id),
            session_hash=hashlib.sha256(request.cookies["horizon_session"].encode()).hexdigest(),
        )
    )
    await session.commit()
    response.set_cookie(
        "horizon_oauth_state",
        state,
        httponly=True,
        secure=settings.production,
        samesite="lax",
        max_age=settings.oauth_state_ttl_seconds,
        path="/",
    )
    return {
        "url": "https://discord.com/api/oauth2/authorize?"
        + urlencode(
            {
                "client_id": settings.discord_client_id,
                "redirect_uri": settings.discord_redirect_uri,
                "response_type": "code",
                "scope": "identify",
                "state": state,
                "prompt": "consent",
            }
        )
    }


async def finish_discord_link(
    request: Request, session: AsyncSession, record: OAuthState, profile: dict[str, Any], settings: Settings
) -> Response:
    cookie_hash = hashlib.sha256(request.cookies.get("horizon_session", "").encode()).hexdigest()
    if cookie_hash != record.session_hash or record.user_id is None:
        raise HTTPException(403, "Привязку нужно завершить в том же аккаунте и браузере")
    user = await locked_user(session, str(record.user_id))
    current = await session.get(UserSession, cookie_hash)
    if (
        current is None
        or current.user_id != user.id
        or current.revoked_at
        or min(current.expires_at, current.absolute_expires_at) <= moment()
    ):
        raise HTTPException(401, "Сессия привязки истекла")
    if record.purpose == "link" and (moment() - current.created_at).total_seconds() > 900:
        raise HTTPException(403, "Повторно подтвердите вход перед привязкой")
    existing = await session.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.provider == "discord", ExternalIdentity.external_id == str(profile["id"])
        )
    )
    own = await session.scalar(
        select(ExternalIdentity).where(ExternalIdentity.provider == "discord", ExternalIdentity.user_id == user.id)
    )
    conflict = (existing is not None and existing.user_id != user.id) or (
        own is not None and own.external_id != str(profile["id"])
    )
    if record.purpose == "reauth" and (own is None or existing is None):
        conflict = True
    record.consumed_at = moment()
    if record.purpose == "link" and not conflict and existing is None and own is None:
        session.add(
            ExternalIdentity(
                user_id=user.id,
                provider="discord",
                external_id=str(profile["id"]),
                username=str(profile.get("username", ""))[:100],
            )
        )
    action = "auth.discord_reauth" if record.purpose == "reauth" else "auth.discord_link"
    audit(session, str(user.id), action + "_conflict" if conflict else action, str(user.id))
    result = "discord-conflict" if conflict else "discord-verified" if record.purpose == "reauth" else "discord-linked"
    response = RedirectResponse(
        str(settings.public_origin).rstrip("/") + "/#/settings?tab=security&auth=" + result, status_code=302
    )
    response.delete_cookie("horizon_oauth_state", path="/")
    response.headers["Cache-Control"] = "no-store"
    if record.purpose == "reauth" and not conflict:
        await issue_session(session, user, request, response, settings, "discord")
    else:
        await session.commit()
    return response


@router.post("/discord/unlink", status_code=204)
async def discord_unlink(
    body: PasswordProof,
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    interactive(principal)
    await rate_limit(session, "credential:" + principal.user_id, 10)
    user = await locked_user(session, principal.user_id)
    credential = await session.get(PasswordCredential, user.id)
    if credential is None:
        raise HTTPException(409, "Сначала добавьте вход по паролю: нельзя удалить последний способ входа")
    if not await verify_password(credential.password_hash, body.password):
        raise HTTPException(401, "Неверный текущий пароль")
    discord = await session.scalar(
        select(ExternalIdentity).where(ExternalIdentity.provider == "discord", ExternalIdentity.user_id == user.id)
    )
    if discord is not None:
        await session.delete(discord)
    await session.execute(text("DELETE FROM auth.discord_outbox WHERE user_id=:user"), {"user": user.id})
    audit(session, principal.user_id, "auth.discord_unlink", principal.user_id)
    response = Response(status_code=204)
    await issue_session(session, user, request, response, settings, "password", revoke_others=True)
    return response

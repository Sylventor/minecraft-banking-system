"""User-facing banking metadata. Financial authority remains exclusively in the core."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .grpc.client import CoreError, GrpcBankingClient
from .models import AuditEvent, ExternalIdentity, MinecraftVerifiedLink, Organization, OrganizationMember, User
from .rbac import permissions as effective_permissions
from .security import Principal

RESERVED_NAMES = frozenset(
    {
        "admin",
        "administrator",
        "support",
        "security",
        "system",
        "horizon",
        "horizonbank",
        "bank",
        "moderator",
        "root",
        "null",
        "official",
        "staff",
    }
)
NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,23}$")


async def rows(session: AsyncSession, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(row) for row in (await session.execute(text(statement), params or {})).mappings()]


def audit(
    session: AsyncSession, actor: str, action: str, target: str, metadata: dict[str, object] | None = None
) -> None:
    session.add(
        AuditEvent(
            actor_id=uuid.UUID(actor),
            action=action,
            target_type=action.split(".")[0],
            target_id=target,
            metadata_json=metadata or {},
        )
    )


async def notify(
    session: AsyncSession,
    user: str,
    key: str,
    kind: str,
    title: str,
    body: str,
    operation: str | None = None,
    created_at: str | None = None,
) -> None:
    # Serialize with profile erasure so late financial events cannot recreate copies.
    allowed = await session.scalar(
        text(
            "SELECT id FROM auth.application_users WHERE id=:u AND erased_at IS NULL "
            "AND processing_restricted_at IS NULL FOR SHARE"
        ),
        {"u": uuid.UUID(user)},
    )
    if allowed is None:
        return
    ident = uuid.uuid4()
    inserted = await session.scalar(
        text(
            "INSERT INTO auth.user_notifications(id,user_id,event_key,kind,title,body,operation_id,created_at) "
            "VALUES(:id,:user,:key,:kind,:title,:body,CAST(:operation AS uuid),COALESCE(CAST(:created AS timestamptz),now())) "
            "ON CONFLICT(user_id,event_key) DO NOTHING RETURNING id"
        ),
        {
            "id": ident,
            "user": uuid.UUID(user),
            "key": key,
            "kind": kind,
            "title": title,
            "body": body,
            "operation": uuid.UUID(operation) if operation else None,
            "created": datetime.fromisoformat(created_at) if created_at else None,
        },
    )
    if inserted and operation is None:
        from .notifications import enqueue

        await enqueue(session, ident, uuid.UUID(user), kind, title, body)


async def assign_username(session: AsyncSession, user: User, requested: str | None = None) -> str:
    if requested is not None:
        candidate = requested.strip()
        if not NAME_PATTERN.fullmatch(candidate) or candidate.casefold() in RESERVED_NAMES:
            raise HTTPException(
                422, "Имя: 3–24 латинских символа, цифры или _, начинается с буквы. Служебные имена недоступны."
            )
    else:
        base = re.sub(r"[^A-Za-z0-9_]", "_", user.display_name).strip("_")[:16]
        if len(base) < 3 or not base[0].isalpha() or base.casefold() in RESERVED_NAMES:
            base = "player"
        candidate = base + "_" + user.id.hex[:6]
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": "bank-name:" + candidate.casefold()}
    )
    owner = await session.scalar(
        select(User.id).where(text("lower(bank_username)=lower(:name)")).params(name=candidate)
    )
    if owner is not None and owner != user.id:
        if requested is not None:
            raise HTTPException(409, "Это банковское имя уже занято")
        candidate = "player_" + user.id.hex[:17]
    previous = user.bank_username
    if previous != candidate:
        user.bank_username = candidate
        await session.execute(
            text("INSERT INTO auth.bank_username_history(id,user_id,old_name,new_name) VALUES(:id,:user,:old,:new)"),
            {"id": uuid.uuid4(), "user": user.id, "old": previous, "new": candidate},
        )
        audit(
            session, str(user.id), "profile.bank_username", str(user.id), {"old_name": previous, "new_name": candidate}
        )
    return candidate


async def profile_view(session: AsyncSession, principal: Principal) -> dict[str, Any]:
    user = await session.get(User, uuid.UUID(principal.user_id))
    if user is None:
        raise HTTPException(401, "Профиль недоступен")
    discord = await session.scalar(
        select(ExternalIdentity).where(ExternalIdentity.user_id == user.id, ExternalIdentity.provider == "discord")
    )
    minecraft = await session.get(MinecraftVerifiedLink, user.id)
    permissions = (
        [] if principal.via_api_key or principal.via_bot else sorted(await effective_permissions(session, user.id))
    )
    preferences = await rows(
        session,
        "SELECT discord_enabled,security_enabled,transfers_enabled,incoming_enabled,outgoing_enabled,organizations_enabled FROM auth.notification_preferences WHERE user_id=:user",
        {"user": user.id},
    )
    return {
        "id": str(user.id),
        "display_name": user.display_name,
        "bank_username": user.bank_username,
        "language": user.language,
        "banking_ready": user.banking_ready,
        "via_api_key": principal.via_api_key,
        "scopes": sorted(principal.scopes),
        "permissions": permissions,
        "discord": {"username": discord.username, "id": discord.external_id} if discord else None,
        "minecraft": {"nickname": minecraft.nickname, "player_uuid": str(minecraft.player_uuid)} if minecraft else None,
        "notifications": preferences[0]
        if preferences
        else {
            "discord_enabled": True,
            "security_enabled": True,
            "transfers_enabled": True,
            "incoming_enabled": True,
            "outgoing_enabled": True,
            "organizations_enabled": True,
        },
        "discord_notifications_available": bool(
            await session.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM auth.notification_worker_status WHERE last_seen_at>now()-interval '2 minutes')"
                )
            )
        ),
    }


async def ready_profile(
    session: AsyncSession, principal: Principal, core: GrpcBankingClient, rid: str, cid: str
) -> dict[str, Any]:
    primary = await core.primary_account(principal.user_id, rid, cid)
    user = await session.get(User, uuid.UUID(principal.user_id))
    if user is None:
        raise HTTPException(401, "Профиль недоступен")
    if not user.bank_username:
        await assign_username(session, user)
    if not user.banking_ready:
        user.banking_ready = True
    await session.commit()
    return primary


async def lookup_limit(session: AsyncSession, user: str) -> None:
    result = await session.execute(
        text(
            "INSERT INTO auth.recipient_lookup_windows(user_id,window_start,used) VALUES(:user,date_trunc('minute',now()),1) "
            "ON CONFLICT(user_id) DO UPDATE SET window_start=EXCLUDED.window_start,used="
            "CASE WHEN auth.recipient_lookup_windows.window_start=EXCLUDED.window_start THEN auth.recipient_lookup_windows.used+1 ELSE 1 END RETURNING used"
        ),
        {"user": uuid.UUID(user)},
    )
    used = int(result.scalar_one())
    await session.commit()  # Invalid searches still consume the shared per-user budget.
    if used > 60:
        raise HTTPException(429, "Слишком частый поиск. Подождите минуту.")


async def recipient_user(session: AsyncSession, user_id: str) -> dict[str, Any]:
    found = await rows(
        session,
        "SELECT u.id,u.display_name,u.bank_username,m.nickname minecraft_nickname,e.username discord_username "
        "FROM auth.application_users u LEFT JOIN auth.minecraft_verified_links m ON m.user_id=u.id "
        "LEFT JOIN auth.external_identities e ON e.user_id=u.id AND e.provider='discord' "
        "WHERE u.id=:user AND u.status='active' AND u.banking_ready",
        {"user": uuid.UUID(user_id)},
    )
    if not found:
        raise HTTPException(404, "Получатель недоступен")
    return {**found[0], "id": str(found[0]["id"]), "kind": "user"}


async def resolve_recipient(
    session: AsyncSession, core: GrpcBankingClient, actor: str, kind: str, value: str, rid: str, cid: str
) -> dict[str, Any]:
    value = value.strip()
    if kind == "account":
        account = await core.resolve_account(actor, rid, cid, identifier=value)
        if account["owner_id"]:
            user = await recipient_user(session, account["owner_id"])
        elif account["organization_id"]:
            org = await session.get(Organization, uuid.UUID(account["organization_id"]))
            if org is None:
                raise HTTPException(404, "Организация недоступна")
            user = {
                "id": None,
                "display_name": org.name,
                "bank_username": None,
                "minecraft_nickname": None,
                "kind": "account",
            }
        else:
            raise HTTPException(404, "Получатель счёта не подтверждён")
    else:
        if kind == "user":
            try:
                recipient_id = str(uuid.UUID(value))
            except ValueError as error:
                raise HTTPException(422, "Выберите получателя из результатов поиска") from error
        else:
            column = {"bank_name": "u.bank_username", "minecraft": "m.nickname", "discord": "e.external_id"}.get(kind)
            if column is None:
                raise HTTPException(422, "Неизвестный способ поиска")
            found = await rows(
                session,
                "SELECT u.id FROM auth.application_users u LEFT JOIN auth.minecraft_verified_links m ON m.user_id=u.id LEFT JOIN auth.external_identities e ON e.user_id=u.id AND e.provider='discord' WHERE u.status='active' AND u.banking_ready AND lower("
                + column
                + ")=lower(:value) LIMIT 2",
                {"value": value.removeprefix("@")},
            )
            if len(found) != 1:
                raise HTTPException(
                    409 if found else 404, "Получатель не найден однозначно. Уточните поиск и выберите пользователя."
                )
            recipient_id = str(found[0]["id"])
        user = await recipient_user(session, recipient_id)
        account = await core.resolve_account(actor, rid, cid, owner_id=recipient_id)
    return {
        **user,
        "account_id": account["account_id"],
        "public_number": account["public_number"],
        "account_name": account["name"],
        "organization_id": account["organization_id"],
        "owner_id": account["owner_id"],
    }


async def search_recipients(
    session: AsyncSession, core: GrpcBankingClient, actor: str, query: str, rid: str, cid: str
) -> list[dict[str, Any]]:
    await lookup_limit(session, actor)
    query = query.strip()
    if len(query) < 3 or len(query) > 80:
        raise HTTPException(422, "Введите не менее трёх символов")
    if query.upper().startswith("HB-") or re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}", query):
        try:
            return [await resolve_recipient(session, core, actor, "account", query, rid, cid)]
        except (CoreError, HTTPException) as error:
            if isinstance(error, CoreError) and error.code != "NOT_FOUND":
                raise
            return []
    discord_id = re.fullmatch(r"(?:<@!?)?([0-9]{15,22})>?", query)
    key = query.removeprefix("@").casefold()
    candidates = await rows(
        session,
        "SELECT DISTINCT u.id,u.bank_username FROM auth.application_users u "
        "LEFT JOIN auth.minecraft_verified_links m ON m.user_id=u.id LEFT JOIN auth.external_identities e ON e.user_id=u.id AND e.provider='discord' "
        "WHERE u.status='active' AND u.banking_ready AND (starts_with(lower(u.bank_username),:query) OR starts_with(lower(m.nickname),:query) "
        "OR starts_with(lower(e.username),:query) OR lower(u.display_name)=:query OR e.external_id=:discord) ORDER BY u.bank_username LIMIT 8",
        {"query": key, "discord": discord_id[1] if discord_id else ""},
    )
    result = []
    for candidate in candidates:
        try:
            result.append(await resolve_recipient(session, core, actor, "user", str(candidate["id"]), rid, cid))
        except (CoreError, HTTPException) as error:
            if isinstance(error, CoreError) and error.code not in {"NOT_FOUND", "PERMISSION_DENIED"}:
                raise
    return result


async def all_accounts(core: GrpcBankingClient, actor: str, rid: str, cid: str) -> list[dict[str, Any]]:
    result = []
    cursor = ""
    for _ in range(50):
        page = await core.accounts(actor, 100, cursor, rid, cid)
        result.extend(page["items"])
        if not page["next_cursor"]:
            return sorted(
                result,
                key=lambda item: (not item["is_primary"], item["organization_id"] != "", item["name"], item["id"]),
            )
        if page["next_cursor"] == cursor:
            raise HTTPException(502, "Некорректная пагинация счетов")
        cursor = page["next_cursor"]
    raise HTTPException(413, "Слишком много счетов для единого кабинета; используйте постраничный API")


async def decorate_operations(
    session: AsyncSession, operations: list[dict[str, Any]], own_accounts: set[str]
) -> list[dict[str, Any]]:
    users = {
        uuid.UUID(item[key])
        for item in operations
        for key in ("source_owner_id", "destination_owner_id")
        if item.get(key)
    }
    names = (
        {
            str(user.id): {"display_name": user.display_name, "bank_username": user.bank_username}
            for user in (await session.scalars(select(User).where(User.id.in_(users)))).all()
        }
        if users
        else {}
    )
    orgs = {
        uuid.UUID(item[key])
        for item in operations
        for key in ("source_organization_id", "destination_organization_id")
        if item.get(key)
    }
    org_names = (
        {
            str(org.id): org.name
            for org in (await session.scalars(select(Organization).where(Organization.id.in_(orgs)))).all()
        }
        if orgs
        else {}
    )
    result = []
    for item in operations:
        source = names.get(item.get("source_owner_id", ""), {})
        destination = names.get(item.get("destination_owner_id", ""), {})
        incoming = item["destination_account_id"] in own_accounts and item["source_account_id"] not in own_accounts
        internal = item["destination_account_id"] in own_accounts and item["source_account_id"] in own_accounts
        source_label = (
            source.get("bank_username")
            or source.get("display_name")
            or org_names.get(item.get("source_organization_id", ""))
            or ("Banking System" if item["type"] == "issue" else item.get("source_name") or "Банковский счёт")
        )
        destination_label = (
            destination.get("bank_username")
            or destination.get("display_name")
            or org_names.get(item.get("destination_organization_id", ""))
            or item.get("destination_name")
            or "Банковский счёт"
        )
        result.append(
            {
                **item,
                "sender": source_label,
                "recipient": destination_label,
                "direction": "internal" if internal else "incoming" if incoming else "outgoing",
                "counterparty": source_label if incoming else destination_label,
            }
        )
    return result


async def ensure_organization(
    session: AsyncSession, core: GrpcBankingClient, actor: str, org_id: uuid.UUID, rid: str, cid: str
) -> dict[str, Any]:
    org = await session.get(Organization, org_id)
    if org is None:
        raise HTTPException(404, "Организация не найдена")
    # The bootstrap payload is read exclusively from the existing trusted auth schema.
    member = await session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org_id, OrganizationMember.user_id == uuid.UUID(actor)
        )
    )
    if member is None:
        raise HTTPException(403, "Вы не участник организации")
    members = [
        {"user_id": str(item.user_id), "role": item.role}
        for item in (
            await session.scalars(select(OrganizationMember).where(OrganizationMember.organization_id == org_id))
        ).all()
    ]
    await core.ensure_organization(str(org.owner_id), str(org.id), org.name, members, rid, cid)
    return await core.organization(actor, str(org.id), rid, cid)


def now() -> datetime:
    return datetime.now(UTC)

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..banking_schemas import (
    AccountSettings,
    OrganizationAccountCreate,
    OrganizationMemberSettings,
    ReadNotifications,
    RecipientReference,
    SavedRecipientRequest,
    TransferQuoteRequest,
    UserSettings,
)
from ..database import get_session
from ..dependencies import get_core, request_ids, request_source, require_idempotency
from ..experience import (
    all_accounts,
    assign_username,
    audit,
    decorate_operations,
    ensure_organization,
    lookup_limit,
    notify,
    now,
    profile_view,
    ready_profile,
    recipient_user,
    resolve_recipient,
    rows,
    search_recipients,
)
from ..grpc.client import CoreError, GrpcBankingClient
from ..models import User, UserSession
from ..security import Principal, current_principal, require_resource, require_scope

router = APIRouter(prefix="/api/v1", tags=["banking"])


def interactive(principal: Principal, *, bot: bool = True) -> None:
    if principal.via_api_key or (principal.via_bot and not bot):
        raise HTTPException(
            403, "Это действие доступно только в личном кабинете" if not bot else "Это действие недоступно API-ключу"
        )


@router.get("/dashboard")
async def dashboard(
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    interactive(principal)
    rid, cid = request_ids(request)
    primary = await ready_profile(session, principal, core, rid, cid)
    accounts = await all_accounts(core, principal.user_id, rid, cid)
    activity = await core.activity(principal.user_id, rid, cid, limit=6)
    personal = [a for a in accounts if a["account_type"] == "personal" and a["owner_id"] == principal.user_id]
    total = sum((Decimal(a["available"]["amount"]) for a in personal), Decimal("0.00"))
    unread = await session.scalar(
        text("SELECT count(*) FROM auth.user_notifications WHERE user_id=:user AND read_at IS NULL"),
        {"user": uuid.UUID(principal.user_id)},
    )
    return {
        "profile": await profile_view(session, principal),
        "primary_account": primary,
        "accounts": accounts,
        "total_available": {"amount": format(total, ".2f"), "currency": "DB"},
        "recent_operations": await decorate_operations(session, activity["items"], {a["id"] for a in accounts}),
        "unread_notifications": int(unread or 0),
    }


@router.get("/accounts/primary")
async def primary(
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    interactive(principal)
    return await ready_profile(session, principal, core, *request_ids(request))


@router.patch("/accounts/{account_id}")
async def account_settings(
    account_id: uuid.UUID,
    body: AccountSettings,
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    interactive(principal)
    result = await core.update_account(
        principal.user_id, str(account_id), body.name or "", body.is_primary, *request_ids(request)
    )
    audit(session, principal.user_id, "account.settings", str(account_id), body.model_dump(exclude_none=True))
    await session.commit()
    return result


@router.patch("/me/settings")
async def update_settings(
    body: UserSettings, principal: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    interactive(principal)
    user = await session.scalar(select(User).where(User.id == uuid.UUID(principal.user_id)).with_for_update())
    if user is None:
        raise HTTPException(401, "Профиль недоступен")
    if body.bank_username is not None and body.bank_username != user.bank_username:
        await assign_username(session, user, body.bank_username)
        await notify(
            session,
            principal.user_id,
            "username:" + str(uuid.uuid4()),
            "security",
            "Банковское имя изменено",
            "Ваше новое банковское имя: " + body.bank_username,
        )
    if body.display_name is not None:
        if not body.display_name.strip() or any(ord(c) < 32 for c in body.display_name):
            raise HTTPException(422, "Введите корректное отображаемое имя")
        user.display_name = body.display_name.strip()
    if body.language:
        user.language = body.language
    await session.execute(
        text("INSERT INTO auth.notification_preferences(user_id) VALUES(:user) ON CONFLICT DO NOTHING"),
        {"user": user.id},
    )
    for key in ("discord_enabled", "security_enabled", "transfers_enabled", "incoming_enabled", "outgoing_enabled", "organizations_enabled"):
        value = getattr(body, key)
        if value is not None:
            await session.execute(
                text("UPDATE auth.notification_preferences SET " + key + "=:value WHERE user_id=:user"),
                {"user": user.id, "value": value},
            )
    audit(session, principal.user_id, "profile.settings", principal.user_id, {"fields": sorted(body.model_fields_set)})
    await session.commit()
    return await profile_view(session, principal)


@router.get("/recipients/search")
async def recipients(
    request: Request,
    q: str = Query(min_length=3, max_length=80),
    principal: Principal = Depends(require_scope("recipients.read")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    return {"items": await search_recipients(session, core, principal.user_id, q, *request_ids(request))}


async def require_internal_account(
    principal: Principal, core: GrpcBankingClient, account: dict[str, Any], rid: str, cid: str
) -> None:
    """Visibility and ownership/membership are independent of a debit mandate."""
    require_resource(principal, account["id"])
    if account["status"] != "active" or not {"accounts.read", "balance.read"}.issubset(account["permissions"]):
        raise HTTPException(403, "Этот счёт недоступен для перевода между счетами")
    if account["account_type"] == "personal" and account["owner_id"] == principal.user_id:
        return
    if account["account_type"] == "organization" and account["organization_id"]:
        organization = await core.organization(principal.user_id, account["organization_id"], rid, cid)
        if any(item["id"] == account["id"] for item in organization["accounts"]):
            return
    raise HTTPException(403, "Выберите свой личный счёт или доступный счёт вашей организации")


@router.post("/transfer-quotes", status_code=201)
async def prepare_transfer(
    body: TransferQuoteRequest,
    request: Request,
    principal: Principal = Depends(require_scope("transfers.create")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    rid, cid = request_ids(request)
    if body.source_account_id:
        source_id = str(body.source_account_id)
    else:
        interactive(principal)
        source_id = (await ready_profile(session, principal, core, rid, cid))["id"]
    require_resource(principal, source_id)
    source = await core.balance(principal.user_id, source_id, rid, cid)
    if "transfer" not in source["permissions"] or source["status"] != "active":
        raise HTTPException(403, "С этого счёта нельзя отправить перевод")
    if Decimal(body.amount) > Decimal(source["available"]["amount"]):
        raise HTTPException(409, "Недостаточно доступных средств")
    await lookup_limit(session, principal.user_id)
    recipient = await resolve_recipient(
        session, core, principal.user_id, body.recipient.kind, body.recipient.value, rid, cid
    )
    if recipient["account_id"] == source_id:
        raise HTTPException(422, "Нельзя перевести деньги на тот же счёт")
    if body.internal:
        if body.recipient.kind != "account":
            raise HTTPException(422, "Для перевода между счетами выберите конкретный счёт получателя")
        await require_internal_account(principal, core, source, rid, cid)
        require_resource(principal, recipient["account_id"])
        destination = await core.balance(principal.user_id, recipient["account_id"], rid, cid)
        await require_internal_account(principal, core, destination, rid, cid)
    sender = await session.get(User, uuid.UUID(principal.user_id))
    snapshot = {
        "recipient": recipient,
        "source": source,
        "sender": sender.bank_username or sender.display_name if sender else "Banking System",
        "internal": body.internal,
    }
    quote_id = uuid.uuid4()
    expires = now() + timedelta(minutes=5)
    import json

    await session.execute(
        text(
            "INSERT INTO auth.transfer_quotes(id,user_id,source_account_id,destination_account_id,recipient_user_id,snapshot,amount,reason,expires_at) VALUES(:id,:user,:source,:destination,:recipient,CAST(:snapshot AS jsonb),:amount,:reason,:expires)"
        ),
        {
            "id": quote_id,
            "user": uuid.UUID(principal.user_id),
            "source": uuid.UUID(source_id),
            "destination": uuid.UUID(recipient["account_id"]),
            "recipient": uuid.UUID(recipient["id"]) if recipient["id"] else None,
            "snapshot": json.dumps(snapshot, ensure_ascii=False),
            "amount": Decimal(body.amount),
            "reason": body.reason,
            "expires": expires,
        },
    )
    await session.commit()
    return {
        "id": str(quote_id),
        "internal": body.internal,
        "source": source,
        "recipient": recipient,
        "amount": {"amount": body.amount, "currency": "DB"},
        "fee": {"amount": "0.00", "currency": "DB"},
        "total": {"amount": body.amount, "currency": "DB"},
        "reason": body.reason,
        "expires_at": expires,
        "idempotency_key": str(quote_id),
    }


@router.post("/recipients/resolve")
async def resolve_public_recipient(
    body: RecipientReference,
    request: Request,
    principal: Principal = Depends(require_scope("recipients.read")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    await lookup_limit(session, principal.user_id)
    return await resolve_recipient(session, core, principal.user_id, body.kind, body.value, *request_ids(request))


@router.get("/transfer-quotes/{quote_id}")
async def get_quote(
    quote_id: uuid.UUID,
    principal: Principal = Depends(require_scope("transfers.create")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    found = await rows(
        session,
        "SELECT * FROM auth.transfer_quotes WHERE id=:id AND user_id=:user",
        {"id": quote_id, "user": uuid.UUID(principal.user_id)},
    )
    if not found:
        raise HTTPException(404, "Подтверждение перевода не найдено")
    quote = found[0]
    require_resource(principal, str(quote["source_account_id"]))
    return {
        "id": str(quote_id),
        "internal": bool(quote["snapshot"].get("internal", False)),
        "source": quote["snapshot"]["source"],
        "recipient": quote["snapshot"]["recipient"],
        "amount": {"amount": format(quote["amount"], ".2f"), "currency": "DB"},
        "fee": {"amount": "0.00", "currency": "DB"},
        "total": {"amount": format(quote["amount"], ".2f"), "currency": "DB"},
        "reason": quote["reason"],
        "expires_at": quote["expires_at"],
        "idempotency_key": str(quote_id),
        "operation_id": str(quote["operation_id"]) if quote["operation_id"] else None,
    }


@router.get("/transfer-drafts")
async def transfer_drafts(
    principal: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    interactive(principal)
    found = await rows(
        session,
        "SELECT id,snapshot,amount,created_at,expires_at FROM auth.transfer_quotes WHERE user_id=:user AND operation_id IS NULL AND created_at>now()-interval '1 day' ORDER BY created_at DESC LIMIT 10",
        {"user": uuid.UUID(principal.user_id)},
    )
    return [
        {
            "id": str(item["id"]),
            "recipient": item["snapshot"]["recipient"]["display_name"],
            "amount": format(item["amount"], ".2f"),
            "created_at": item["created_at"],
            "expires_at": item["expires_at"],
        }
        for item in found
    ]


async def quote_receipt(
    session: AsyncSession,
    core: GrpcBankingClient,
    quote: dict[str, Any],
    principal: Principal,
    rid: str,
    cid: str,
    operation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if operation is None:
        operation = await core.request_outcome(principal.user_id, "quote:" + str(quote["id"]), rid, cid)
    snapshot = quote["snapshot"]
    return {
        **operation,
        "sender": snapshot["sender"],
        "recipient": snapshot["recipient"]["bank_username"] or snapshot["recipient"]["display_name"],
        "recipient_profile": snapshot["recipient"],
        "source_name": snapshot["source"]["name"],
        "source_public_number": snapshot["source"]["public_number"],
        "destination_public_number": snapshot["recipient"]["public_number"],
        "reason": quote["reason"],
        "expires_at": quote["expires_at"].isoformat(),
        "direction": "internal" if snapshot.get("internal") else "outgoing",
        "quote_id": str(quote["id"]),
    }


@router.post("/transfer-quotes/{quote_id}/confirm")
async def confirm_transfer(
    quote_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_scope("transfers.create")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    if require_idempotency(request) != str(quote_id):
        raise HTTPException(409, "Ключ подтверждения не соответствует выбранному переводу")
    rid, cid = request_ids(request)
    found = await rows(
        session,
        "SELECT * FROM auth.transfer_quotes WHERE id=:id AND user_id=:user FOR UPDATE",
        {"id": quote_id, "user": uuid.UUID(principal.user_id)},
    )
    if not found:
        raise HTTPException(404, "Подтверждение перевода не найдено")
    quote = found[0]
    require_resource(principal, str(quote["source_account_id"]))
    if quote["operation_id"]:
        return await quote_receipt(session, core, quote, principal, rid, cid)
    operation = None
    try:
        operation = await core.request_outcome(principal.user_id, "quote:" + str(quote_id), rid, cid)
    except CoreError as error:
        if error.code != "NOT_FOUND":
            raise
    if operation is not None and (
        operation["source_account_id"] != str(quote["source_account_id"])
        or operation["destination_account_id"] != str(quote["destination_account_id"])
        or operation["amount"]["amount"] != format(quote["amount"], ".2f")
        or operation.get("reason", "") != quote["reason"]
    ):
        raise HTTPException(409, "Ключ подтверждения уже связан с другой операцией. Обратитесь в поддержку.")
    if operation is None:
        if quote["expires_at"] <= now():
            raise HTTPException(410, "Время подтверждения истекло. Проверьте данные и подготовьте перевод заново.")
        if quote["snapshot"].get("internal"):
            for account_id in (str(quote["source_account_id"]), str(quote["destination_account_id"])):
                require_resource(principal, account_id)
                current = await core.balance(principal.user_id, account_id, rid, cid)
                await require_internal_account(principal, core, current, rid, cid)
        destination = await core.resolve_account(
            principal.user_id, rid, cid, identifier=str(quote["destination_account_id"])
        )
        if (
            destination["owner_id"] != quote["snapshot"]["recipient"]["owner_id"]
            or destination["organization_id"] != quote["snapshot"]["recipient"]["organization_id"]
        ):
            raise HTTPException(409, "Реквизиты получателя изменились. Подготовьте перевод заново.")
        if quote["recipient_user_id"]:
            await recipient_user(session, str(quote["recipient_user_id"]))
        try:
            operation = await core.money_operation(
                "Transfer",
                principal.user_id,
                {
                    "source_account_id": str(quote["source_account_id"]),
                    "destination_account_id": str(quote["destination_account_id"]),
                    "amount": format(quote["amount"], ".2f"),
                    "reason": quote["reason"],
                    "expires_at": quote["expires_at"].isoformat(),
                },
                "quote:" + str(quote_id),
                rid,
                cid,
                request_source(principal),
            )
        except CoreError as error:
            uncertain = error.code in {"UNAVAILABLE", "DEADLINE_EXCEEDED", "INTERNAL", "UNKNOWN"}
            await notify(
                session,
                principal.user_id,
                "transfer-attempt:" + str(quote_id) + ":" + error.code,
                "pending" if uncertain else "failed",
                "Статус перевода уточняется" if uncertain else "Перевод не выполнен",
                "Повторите то же подтверждение: банк проверит результат без повторного списания."
                if uncertain
                else "Средства не списаны. Проверьте баланс и состояние счёта.",
            )
            await session.commit()
            raise
    await session.execute(
        text("UPDATE auth.transfer_quotes SET operation_id=:operation WHERE id=:id"),
        {"operation": uuid.UUID(operation["id"]), "id": quote_id},
    )
    key = (
        "user:" + str(quote["recipient_user_id"])
        if quote["recipient_user_id"]
        else "account:" + str(quote["destination_account_id"])
    )
    await session.execute(
        text(
            "INSERT INTO auth.recent_recipients(user_id,recipient_key,recipient_user_id,account_id) VALUES(:user,:key,:recipient,:account) ON CONFLICT(user_id,recipient_key) DO UPDATE SET account_id=EXCLUDED.account_id,last_used_at=now()"
        ),
        {
            "user": uuid.UUID(principal.user_id),
            "key": key,
            "recipient": quote["recipient_user_id"],
            "account": quote["destination_account_id"],
        },
    )
    await notify(
        session,
        principal.user_id,
        "operation:" + operation["id"],
        "transfer",
        "Перевод выполнен",
        format(quote["amount"], ".2f") + " ДБ · " + quote["snapshot"]["recipient"]["display_name"],
        operation["id"],
        operation["created_at"],
    )
    if quote["recipient_user_id"]:
        await notify(
            session,
            str(quote["recipient_user_id"]),
            "operation:" + operation["id"],
            "transfer",
            "Входящий перевод",
            format(quote["amount"], ".2f") + " ДБ · " + quote["snapshot"]["sender"],
            operation["id"],
            operation["created_at"],
        )
    audit(session, principal.user_id, "transfer.confirm", operation["id"], {"quote_id": str(quote_id)})
    await session.commit()
    quote["operation_id"] = operation["id"]
    return await quote_receipt(session, core, quote, principal, rid, cid, operation)


@router.get("/activity")
async def activity(
    request: Request,
    account_id: uuid.UUID | None = None,
    limit: int = Query(20, ge=1, le=100),
    cursor: uuid.UUID | None = None,
    operation_type: Literal["", "transfer", "payment", "refund", "hold", "release_hold", "capture_hold", "issue"] = "",
    q: str = Query("", max_length=100),
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    order: Literal["asc", "desc"] = "desc",
    principal: Principal = Depends(require_scope("history.read")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    rid, cid = request_ids(request)
    if principal.via_api_key and account_id is None:
        raise HTTPException(403, "Для API-ключа укажите разрешённый счёт")
    if account_id:
        require_resource(principal, str(account_id))
        await core.history(principal.user_id, str(account_id), 1, rid, cid)
    page = await core.activity(
        principal.user_id,
        rid,
        cid,
        account_id=str(account_id) if account_id else "",
        limit=limit,
        cursor=str(cursor) if cursor else "",
        operation_type=operation_type,
        query=q,
        from_date=from_date.isoformat() if from_date else "",
        to_date=to_date.isoformat() if to_date else "",
        order=order,
    )
    accounts = await all_accounts(core, principal.user_id, rid, cid)
    return {
        "items": await decorate_operations(session, page["items"], {a["id"] for a in accounts}),
        "next_cursor": page["next_cursor"],
    }


@router.get("/receipts/{operation_id}")
async def receipt(
    operation_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_scope("history.read")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    rid, cid = request_ids(request)
    operation = await core.operation(principal.user_id, str(operation_id), rid, cid)
    if principal.via_api_key and not {operation["source_account_id"], operation["destination_account_id"]}.intersection(
        principal.account_ids or ()
    ):
        raise HTTPException(403, "Ключ не даёт доступа к этой квитанции")
    quotes = await rows(
        session, "SELECT * FROM auth.transfer_quotes WHERE operation_id=:operation", {"operation": operation_id}
    )
    if quotes and str(quotes[0]["user_id"]) == principal.user_id:
        return await quote_receipt(session, core, quotes[0], principal, rid, cid, operation)
    accounts = await all_accounts(core, principal.user_id, rid, cid)
    return (await decorate_operations(session, [operation], {a["id"] for a in accounts}))[0]


@router.get("/recipients/saved")
async def saved_recipients(
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> list[dict[str, Any]]:
    interactive(principal)
    saved = await rows(
        session,
        "SELECT id,recipient_user_id,account_id,label FROM auth.saved_recipients WHERE user_id=:user ORDER BY created_at DESC LIMIT 100",
        {"user": uuid.UUID(principal.user_id)},
    )
    result = []
    for item in saved:
        try:
            recipient = await resolve_recipient(
                session,
                core,
                principal.user_id,
                "user" if item["recipient_user_id"] else "account",
                str(item["recipient_user_id"] or item["account_id"]),
                *request_ids(request),
            )
            result.append({**recipient, "saved_id": str(item["id"]), "label": item["label"], "available": True})
        except (HTTPException, CoreError):
            result.append(
                {
                    "saved_id": str(item["id"]),
                    "label": item["label"],
                    "available": False,
                    "display_name": item["label"] or "Получатель недоступен",
                }
            )
    return result


@router.post("/recipients/saved", status_code=201)
async def save_recipient(
    body: SavedRecipientRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, str]:
    interactive(principal)
    await lookup_limit(session, principal.user_id)
    recipient = await resolve_recipient(
        session, core, principal.user_id, body.recipient.kind, body.recipient.value, *request_ids(request)
    )
    recipient_id = uuid.UUID(recipient["id"]) if body.recipient.kind != "account" and recipient["id"] else None
    params = {
        "user": uuid.UUID(principal.user_id),
        "recipient": recipient_id,
        "account": None if recipient_id else uuid.UUID(recipient["account_id"]),
        "label": body.label,
        "id": uuid.uuid4(),
    }
    existing = await rows(
        session,
        "SELECT id FROM auth.saved_recipients WHERE user_id=:user AND (recipient_user_id=:recipient OR account_id=:account)",
        params,
    )
    if existing:
        return {"id": str(existing[0]["id"])}
    count = await session.scalar(text("SELECT count(*) FROM auth.saved_recipients WHERE user_id=:user"), params)
    if int(count or 0) >= 100:
        raise HTTPException(422, "Можно сохранить не более 100 получателей")
    await session.execute(
        text(
            "INSERT INTO auth.saved_recipients(id,user_id,recipient_user_id,account_id,label) VALUES(:id,:user,:recipient,:account,:label)"
        ),
        params,
    )
    await session.commit()
    return {"id": str(params["id"])}


@router.delete("/recipients/saved/{saved_id}", status_code=204)
async def unsave_recipient(
    saved_id: uuid.UUID, principal: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)
) -> Response:
    interactive(principal)
    await session.execute(
        text("DELETE FROM auth.saved_recipients WHERE id=:id AND user_id=:user"),
        {"id": saved_id, "user": uuid.UUID(principal.user_id)},
    )
    await session.commit()
    return Response(status_code=204)


@router.get("/recipients/recent")
async def recent_recipients(
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> list[dict[str, Any]]:
    interactive(principal)
    recent = await rows(
        session,
        "SELECT recipient_user_id,account_id FROM auth.recent_recipients WHERE user_id=:user ORDER BY last_used_at DESC LIMIT 8",
        {"user": uuid.UUID(principal.user_id)},
    )
    result = []
    for item in recent:
        try:
            result.append(
                await resolve_recipient(
                    session,
                    core,
                    principal.user_id,
                    "user" if item["recipient_user_id"] else "account",
                    str(item["recipient_user_id"] or item["account_id"]),
                    *request_ids(request),
                )
            )
        except (HTTPException, CoreError):
            continue
    return result


@router.get("/notifications")
async def notifications(
    request: Request,
    limit: int = Query(30, ge=1, le=100),
    cursor: uuid.UUID | None = None,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    interactive(principal)
    rid, cid = request_ids(request)
    # Reconcile notifications from immutable real activity, including transfers made through legacy API.
    recent = await core.activity(principal.user_id, rid, cid, limit=100)
    accounts = await all_accounts(core, principal.user_id, rid, cid)
    for item in await decorate_operations(session, recent["items"], {a["id"] for a in accounts}):
        if item["type"] in {"transfer", "payment", "refund", "issue"}:
            await notify(
                session,
                principal.user_id,
                "operation:" + item["id"],
                "transfer",
                "Поступление средств" if item["direction"] == "incoming" else "Операция по счёту",
                item["amount"]["amount"] + " ДБ · " + item["counterparty"],
                item["id"],
                item["created_at"],
            )
    await session.commit()
    pref = await rows(
        session,
        "SELECT security_enabled,transfers_enabled FROM auth.notification_preferences WHERE user_id=:user",
        {"user": uuid.UUID(principal.user_id)},
    )
    p = pref[0] if pref else {"security_enabled": True, "transfers_enabled": True}
    values = {
        "user": uuid.UUID(principal.user_id),
        "cursor": cursor,
        "limit": limit + 1,
        "security": p["security_enabled"],
        "transfers": p["transfers_enabled"],
    }
    found = await rows(
        session,
        "SELECT id,kind,title,body,operation_id,created_at,read_at FROM auth.user_notifications n WHERE user_id=:user AND (kind<>'security' OR :security) AND (kind<>'transfer' OR :transfers) AND (CAST(:cursor AS uuid) IS NULL OR (created_at,id)<(SELECT created_at,id FROM auth.user_notifications WHERE id=:cursor AND user_id=:user)) ORDER BY created_at DESC,id DESC LIMIT :limit",
        values,
    )
    unread = await session.scalar(
        text(
            "SELECT count(*) FROM auth.user_notifications WHERE user_id=:user AND read_at IS NULL AND (kind<>'security' OR :security) AND (kind<>'transfer' OR :transfers)"
        ),
        values,
    )
    return {
        "items": found[:limit],
        "next_cursor": str(found[limit - 1]["id"]) if len(found) > limit else None,
        "unread": int(unread or 0),
        "discord_delivery_available": bool(await session.scalar(text("SELECT EXISTS(SELECT 1 FROM auth.notification_worker_status WHERE last_seen_at>now()-interval '2 minutes')"))),
    }


@router.post("/notifications/read", status_code=204)
async def mark_read(
    body: ReadNotifications,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    interactive(principal)
    await session.execute(
        text(
            "UPDATE auth.user_notifications SET read_at=COALESCE(read_at,now()) WHERE user_id=:user AND (:all OR id=ANY(CAST(:ids AS uuid[])))"
        ),
        {"user": uuid.UUID(principal.user_id), "all": body.all, "ids": body.ids},
    )
    await session.commit()
    return Response(status_code=204)


@router.get("/me/sessions")
async def sessions(
    request: Request, principal: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    interactive(principal, bot=False)
    current = hashlib.sha256(request.cookies.get("horizon_session", "").encode()).hexdigest()
    found = await rows(
        session,
        "SELECT public_id,token_hash,created_at,last_activity_at,absolute_expires_at,client_info,expires_at FROM auth.user_sessions WHERE user_id=:user AND revoked_at IS NULL AND expires_at>now() AND absolute_expires_at>now() ORDER BY created_at DESC",
        {"user": uuid.UUID(principal.user_id)},
    )
    return [
        {
            "id": str(item["public_id"]),
            "current": item["token_hash"] == current,
            "created_at": item["created_at"],
            "expires_at": item["expires_at"],
            "last_activity_at": item["last_activity_at"],
            "absolute_expires_at": item["absolute_expires_at"],
            "client_info": item["client_info"],
        }
        for item in found
    ]


@router.delete("/me/sessions/{session_id}", status_code=204)
async def revoke_session(
    session_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    interactive(principal, bot=False)
    records = (
        await session.scalars(
            select(UserSession)
            .where(UserSession.user_id == uuid.UUID(principal.user_id), UserSession.revoked_at.is_(None))
            .with_for_update()
        )
    ).all()
    for record in records:
        if record.public_id == session_id:
            record.revoked_at = now()
            audit(session, principal.user_id, "security.session.revoke", str(session_id))
            await notify(
                session,
                principal.user_id,
                "session-revoke:" + str(session_id),
                "security",
                "Сессия завершена",
                "Вы завершили одну из активных сессий банка.",
            )
            await session.commit()
            return Response(status_code=204)
    raise HTTPException(404, "Сессия не найдена")


async def organization_result(session: AsyncSession, value: dict[str, Any]) -> dict[str, Any]:
    ids = [uuid.UUID(m["user_id"]) for m in value["members"]]
    users = {str(u.id): u for u in (await session.scalars(select(User).where(User.id.in_(ids)))).all()}
    value["members"] = [
        {
            **m,
            "display_name": users[m["user_id"]].display_name if m["user_id"] in users else "Участник",
            "bank_username": users[m["user_id"]].bank_username if m["user_id"] in users else None,
        }
        for m in value["members"]
    ]
    return value


@router.get("/organizations/{organization_id}")
async def organization(
    organization_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_scope("organizations.manage")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    interactive(principal)
    value = await ensure_organization(session, core, principal.user_id, organization_id, *request_ids(request))
    return await organization_result(session, value)


@router.post("/organizations/{organization_id}/accounts", status_code=201)
async def organization_account(
    organization_id: uuid.UUID,
    body: OrganizationAccountCreate,
    request: Request,
    principal: Principal = Depends(require_scope("organizations.manage")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    interactive(principal)
    await ensure_organization(session, core, principal.user_id, organization_id, *request_ids(request))
    account = await core.organization_account(
        principal.user_id, str(organization_id), body.name, require_idempotency(request), *request_ids(request)
    )
    audit(
        session, principal.user_id, "organization.account.create", str(organization_id), {"account_id": account["id"]}
    )
    await session.commit()
    return account


@router.put("/organizations/{organization_id}/members/{user_id}")
async def organization_member(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    body: OrganizationMemberSettings,
    request: Request,
    principal: Principal = Depends(require_scope("organizations.manage")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> dict[str, Any]:
    interactive(principal)
    if user_id != body.user_id:
        raise HTTPException(422, "Участник в адресе и теле запроса не совпадает")
    user = await session.get(User, user_id)
    if user is None or user.status != "active":
        raise HTTPException(404, "Пользователь не найден")
    await ensure_organization(session, core, principal.user_id, organization_id, *request_ids(request))
    value = await core.organization_member(
        principal.user_id, str(organization_id), body.model_dump(mode="json"), False, *request_ids(request)
    )
    await session.execute(
        text(
            "INSERT INTO auth.organization_members(id,organization_id,user_id,role) VALUES(:id,:org,:user,:role) ON CONFLICT(organization_id,user_id) DO UPDATE SET role=EXCLUDED.role"
        ),
        {"id": uuid.uuid4(), "org": organization_id, "user": user_id, "role": body.role},
    )
    audit(session, principal.user_id, "organization.member.update", str(organization_id), body.model_dump(mode="json"))
    await session.commit()
    return await organization_result(session, value)


@router.delete("/organizations/{organization_id}/members/{user_id}", status_code=204)
async def remove_member(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_scope("organizations.manage")),
    session: AsyncSession = Depends(get_session),
    core: GrpcBankingClient = Depends(get_core),
) -> Response:
    interactive(principal)
    await ensure_organization(session, core, principal.user_id, organization_id, *request_ids(request))
    await core.organization_member(
        principal.user_id,
        str(organization_id),
        {"user_id": str(user_id), "role": "member", "permissions": []},
        True,
        *request_ids(request),
    )
    await session.execute(
        text("DELETE FROM auth.organization_members WHERE organization_id=:org AND user_id=:user"),
        {"org": organization_id, "user": user_id},
    )
    audit(session, principal.user_id, "organization.member.remove", str(organization_id), {"user_id": str(user_id)})
    await session.commit()
    return Response(status_code=204)

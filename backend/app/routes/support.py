"""Private, session-only support. No bank balances or financial mutations."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..database import get_session
from ..experience import audit, notify, rows
from ..rbac import permissions
from ..schemas import StrictModel
from ..security import Principal, current_principal

router = APIRouter(prefix="/api/v1", tags=["Support"])
Category = Literal["account", "payments", "atm", "organizations", "exchange", "website", "feedback", "other"]
Status = Literal["open", "working", "waiting", "resolved", "closed"]
LABELS = {
    "open": "Открыто",
    "working": "В работе",
    "waiting": "Ожидает ответа пользователя",
    "resolved": "Решено",
    "closed": "Закрыто",
}


class Message(StrictModel):
    body: str = Field(min_length=1, max_length=4000)
    client_id: uuid.UUID

    @field_validator("body")
    @classmethod
    def clean(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise ValueError("Напишите сообщение без управляющих символов")
        return value


class Create(Message):
    title: str = Field(min_length=3, max_length=160)
    category: Category

    @field_validator("title")
    @classmethod
    def title_text(cls, value: str) -> str:
        if len(value.strip()) < 3 or any(ord(c) < 32 for c in value):
            raise ValueError("Укажите тему обращения")
        return value.strip()


class Change(StrictModel):
    status: Status | None = None
    assigned_to: uuid.UUID | None = None


class Read(StrictModel):
    through_id: int = Field(ge=0)


async def interactive(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.via_api_key or principal.via_bot:
        raise HTTPException(403, "Поддержка доступна после входа на сайт")
    return principal


async def staff(session: AsyncSession, principal: Principal, permission: str = "read") -> None:
    grants = await permissions(session, principal.user_id)
    if "admin.support.read" not in grants or "admin.support." + permission not in grants:
        raise HTTPException(403, "Нет разрешения на это действие в поддержке")


async def ticket(
    session: AsyncSession, ident: uuid.UUID, principal: Principal, admin: bool, lock: bool = False
) -> dict[str, Any]:
    if admin:
        await staff(session, principal)
    found = await rows(
        session,
        "SELECT * FROM auth.support_tickets WHERE id=:id "
        + ("" if admin else "AND user_id=:user ")
        + ("FOR UPDATE" if lock else ""),
        {"id": ident, "user": uuid.UUID(principal.user_id)},
    )
    if not found:
        raise HTTPException(404, "Обращение не найдено")
    return found[0]


async def rate(session: AsyncSession, user: str, creating: bool = False, check: bool = True) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": "support:" + user})
    if not check:
        return
    count = await session.scalar(
        text(
            "SELECT count(*) FROM auth.support_tickets WHERE user_id=:u AND created_at>now()-interval '1 hour'"
            if creating
            else "SELECT count(*) FROM (SELECT created_at FROM auth.support_messages WHERE author_id=:u "
            "UNION ALL SELECT created_at FROM auth.support_internal_notes WHERE author_id=:u) m "
            "WHERE created_at>now()-interval '1 minute'"
        ),
        {"u": uuid.UUID(user)},
    )
    if int(count or 0) >= (5 if creating else 20):
        raise HTTPException(429, "Слишком много сообщений. Подождите перед следующей отправкой")


async def event(
    session: AsyncSession,
    t: dict[str, Any],
    actor: str,
    body: str,
    kind: str = "system",
    client_id: uuid.UUID | None = None,
) -> int:
    ident = await session.scalar(
        text(
            "INSERT INTO auth.support_messages(ticket_id,author_id,sender_kind,body,client_id) "
            "VALUES(:t,:a,:k,:b,:c) RETURNING id"
        ),
        {"t": t["id"], "a": uuid.UUID(actor), "k": kind, "b": body, "c": client_id},
    )
    await session.execute(
        text("UPDATE auth.support_tickets SET updated_at=clock_timestamp() WHERE id=:id"), {"id": t["id"]}
    )
    return int(ident)


async def alert(session: AsyncSession, t: dict[str, Any], ident: int, actor: str, to_user: bool) -> None:
    if to_user:
        recipients = [t["user_id"]]
    else:
        recipients = list(
            (
                await session.scalars(
                    text(
                        "SELECT DISTINCT u.id FROM auth.application_users u JOIN auth.effective_permissions p ON p.user_id=u.id "
                        "WHERE u.status='active' AND p.permission='admin.support.read' "
                        "AND (CAST(:assigned AS uuid) IS NULL OR u.id=:assigned)"
                    ),
                    {"assigned": t["assigned_to"]},
                )
            ).all()
        )
    for user in recipients:
        if str(user) != actor:
            await notify(
                session,
                str(user),
                f"support:{ident}",
                "support",
                f"Поддержка · обращение №{t['number']}",
                "Новое сообщение или изменение статуса. Откройте чат поддержки на сайте.",
            )


async def detail(session: AsyncSession, ident: uuid.UUID, principal: Principal, admin: bool) -> dict[str, Any]:
    t = await ticket(session, ident, principal, admin)
    result = dict(t)
    result.pop("request_id", None)
    result["messages"] = await rows(
        session,
        "SELECT m.id,m.author_id,m.sender_kind,m.body,m.created_at,u.display_name AS author "
        "FROM auth.support_messages m JOIN auth.application_users u ON u.id=m.author_id "
        "WHERE m.ticket_id=:id ORDER BY m.id DESC LIMIT 100",
        {"id": ident},
    )
    result["messages"].reverse()
    result["has_older"] = len(result["messages"]) == 100
    if admin:
        result["notes"] = await rows(
            session,
            "SELECT n.id,n.author_id,n.body,n.created_at,u.display_name AS author FROM auth.support_internal_notes n "
            "JOIN auth.application_users u ON u.id=n.author_id WHERE n.ticket_id=:id ORDER BY n.id DESC LIMIT 100",
            {"id": ident},
        )
        result["notes"].reverse()
        result["notes_has_older"] = len(result["notes"]) == 100
    return result


@router.get("/support/tickets")
@router.get("/admin/support/tickets")
async def listing(
    request: Request,
    status: Status | None = None,
    category: Category | None = None,
    q: str = "",
    offset: int = 0,
    principal: Principal = Depends(interactive),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    admin = "/admin/" in request.url.path
    if admin:
        await staff(session, principal)
    if len(q) > 160 or not 0 <= offset <= 100000:
        raise HTTPException(422, "Некорректный фильтр обращений")
    data = await rows(
        session,
        """
      SELECT t.id,t.number,t.title,t.category,t.status,t.assigned_to,t.updated_at,u.display_name AS user_name,
        (SELECT body FROM auth.support_messages m WHERE m.ticket_id=t.id ORDER BY id DESC LIMIT 1) AS last_message,
        (SELECT count(*) FROM auth.support_messages m WHERE m.ticket_id=t.id AND m.author_id<>:user AND m.sender_kind<>'system'
          AND (NOT :admin OR m.sender_kind='user') AND m.id>COALESCE((SELECT last_message_id
          FROM auth.support_reads r WHERE r.ticket_id=t.id AND r.user_id=:user),0)) AS unread
      FROM auth.support_tickets t JOIN auth.application_users u ON u.id=t.user_id
      WHERE (:admin OR t.user_id=:user) AND (CAST(:status AS text) IS NULL OR t.status=:status)
        AND (CAST(:category AS text) IS NULL OR t.category=:category)
        AND (:q='' OR position(lower(:q) in lower(t.title||' '||u.display_name||' '||
          COALESCE(u.bank_username,'')||' '||t.number::text))>0 OR EXISTS
          (SELECT 1 FROM auth.support_messages m WHERE m.ticket_id=t.id AND position(lower(:q) in lower(m.body))>0))
      ORDER BY t.updated_at DESC,t.id LIMIT 31 OFFSET :offset
    """,
        {
            "user": uuid.UUID(principal.user_id),
            "admin": admin,
            "status": status,
            "category": category,
            "q": q.strip(),
            "offset": offset,
        },
    )
    return {"items": data[:30], "next_offset": offset + 30 if len(data) > 30 else None}


@router.get("/support/unread")
async def unread(
    principal: Principal = Depends(interactive), session: AsyncSession = Depends(get_session)
) -> dict[str, int]:
    count = await session.scalar(
        text("""SELECT count(*) FROM auth.support_messages m
      JOIN auth.support_tickets t ON t.id=m.ticket_id WHERE t.user_id=:u AND m.author_id<>:u AND m.sender_kind='staff'
      AND m.id>COALESCE((SELECT last_message_id FROM auth.support_reads WHERE ticket_id=t.id AND user_id=:u),0)"""),
        {"u": uuid.UUID(principal.user_id)},
    )
    return {"unread": int(count or 0)}


@router.post("/support/tickets", status_code=201)
async def create(
    body: Create, principal: Principal = Depends(interactive), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    await rate(session, principal.user_id, True, check=False)
    existing = await rows(
        session,
        "SELECT * FROM auth.support_tickets WHERE user_id=:u AND request_id=:c",
        {"u": uuid.UUID(principal.user_id), "c": body.client_id},
    )
    if existing:
        t = existing[0]
        first = await session.scalar(
            text("SELECT body FROM auth.support_messages WHERE ticket_id=:id ORDER BY id LIMIT 1"), {"id": t["id"]}
        )
        if t["title"] != body.title or t["category"] != body.category or first != body.body:
            raise HTTPException(409, "Ключ обращения уже использован с другим содержимым")
        return await detail(session, t["id"], principal, False)
    await rate(session, principal.user_id, True)
    t = (
        await rows(
            session,
            "INSERT INTO auth.support_tickets(id,user_id,request_id,title,category) VALUES(:id,:u,:c,:t,:cat) RETURNING *",
            {
                "id": uuid.uuid4(),
                "u": uuid.UUID(principal.user_id),
                "c": body.client_id,
                "t": body.title,
                "cat": body.category,
            },
        )
    )[0]
    ident = await event(session, t, principal.user_id, body.body, "user", body.client_id)
    await alert(session, t, ident, principal.user_id, False)
    audit(session, principal.user_id, "support.create", str(t["id"]))
    await session.commit()
    return await detail(session, t["id"], principal, False)


@router.get("/support/tickets/{ident}")
@router.get("/admin/support/tickets/{ident}")
async def get_ticket(
    ident: uuid.UUID,
    request: Request,
    principal: Principal = Depends(interactive),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await detail(session, ident, principal, "/admin/" in request.url.path)


@router.get("/support/tickets/{ident}/messages")
@router.get("/admin/support/tickets/{ident}/messages")
@router.get("/admin/support/tickets/{ident}/notes")
async def history(
    ident: uuid.UUID,
    request: Request,
    before: int,
    principal: Principal = Depends(interactive),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ticket(session, ident, principal, "/admin/" in request.url.path)
    note = request.url.path.endswith("/notes")
    table = "support_internal_notes" if note else "support_messages"
    kind = "'note'" if note else "m.sender_kind"
    data = await rows(
        session,
        f"SELECT m.id,m.author_id,{kind} AS sender_kind,m.body,m.created_at,u.display_name AS author "
        f"FROM auth.{table} m JOIN auth.application_users u ON u.id=m.author_id "
        "WHERE ticket_id=:id AND m.id<:before ORDER BY m.id DESC LIMIT 100",
        {"id": ident, "before": before},
    )
    return {"items": list(reversed(data)), "has_older": len(data) == 100}


@router.post("/support/tickets/{ident}/messages")
@router.post("/admin/support/tickets/{ident}/messages")
@router.post("/admin/support/tickets/{ident}/notes")
async def send(
    ident: uuid.UUID,
    body: Message,
    request: Request,
    principal: Principal = Depends(interactive),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    admin, note = "/admin/" in request.url.path, request.url.path.endswith("/notes")
    if admin:
        await staff(session, principal, "reply")
    await rate(session, principal.user_id, check=False)
    t = await ticket(session, ident, principal, admin, True)
    table = "support_internal_notes" if note else "support_messages"
    old = await rows(
        session,
        f"SELECT ticket_id,body FROM auth.{table} WHERE author_id=:u AND client_id=:c",
        {"u": uuid.UUID(principal.user_id), "c": body.client_id},
    )
    if old:
        if old[0]["ticket_id"] != ident or old[0]["body"] != body.body:
            raise HTTPException(409, "Ключ сообщения уже использован с другим содержимым")
        return await detail(session, ident, principal, admin)
    await rate(session, principal.user_id)
    if t["status"] in {"resolved", "closed"} and not note:
        raise HTTPException(409, "Сначала повторно откройте обращение")
    if note:
        await session.execute(
            text("INSERT INTO auth.support_internal_notes(ticket_id,author_id,client_id,body) VALUES(:t,:a,:c,:b)"),
            {"t": ident, "a": uuid.UUID(principal.user_id), "c": body.client_id, "b": body.body},
        )
        await session.execute(
            text("UPDATE auth.support_tickets SET updated_at=clock_timestamp() WHERE id=:id"), {"id": ident}
        )
    else:
        mid = await event(session, t, principal.user_id, body.body, "staff" if admin else "user", body.client_id)
        target = "waiting" if admin else "working" if t["status"] == "waiting" else t["status"]
        if t["status"] != target:
            await set_status(session, t, principal.user_id, target)
        await alert(session, t, mid, principal.user_id, admin)
    audit(session, principal.user_id, "support.note" if note else "support.message", str(ident))
    await session.commit()
    return await detail(session, ident, principal, admin)


async def set_status(session: AsyncSession, t: dict[str, Any], actor: str, status: str) -> int:
    await session.execute(
        text(
            "UPDATE auth.support_tickets SET status=:s,closed_at=CASE WHEN :s IN ('resolved','closed') "
            "THEN clock_timestamp() ELSE NULL END WHERE id=:id"
        ),
        {"s": status, "id": t["id"]},
    )
    ident = await event(session, t, actor, f"Статус: {LABELS[t['status']]} → {LABELS[status]}")
    t["status"] = status
    return ident


@router.patch("/support/tickets/{ident}")
@router.patch("/admin/support/tickets/{ident}")
async def change(
    ident: uuid.UUID,
    body: Change,
    request: Request,
    principal: Principal = Depends(interactive),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    admin = "/admin/" in request.url.path
    if admin:
        await staff(session, principal, "manage")
    await rate(session, principal.user_id)
    t = await ticket(session, ident, principal, admin, True)
    if not admin and ("assigned_to" in body.model_fields_set or body.status not in {"open", "closed"}):
        raise HTTPException(403, "Пользователь может только закрыть или повторно открыть своё обращение")
    if not admin and body.status == "open" and t["status"] not in {"resolved", "closed", "open"}:
        raise HTTPException(409, "Обращение уже открыто")
    if body.status and body.status != t["status"]:
        mid = await set_status(session, t, principal.user_id, body.status)
        await alert(session, t, mid, principal.user_id, admin)
    if admin and "assigned_to" in body.model_fields_set and body.assigned_to != t["assigned_to"]:
        if body.assigned_to:
            grants = await permissions(session, str(body.assigned_to))
            active = await session.scalar(
                text("SELECT id FROM auth.application_users WHERE id=:u AND status='active'"), {"u": body.assigned_to}
            )
            if not active or not {"admin.support.read", "admin.support.reply"} <= grants:
                raise HTTPException(422, "Выберите действующего сотрудника поддержки")
        await session.execute(
            text("UPDATE auth.support_tickets SET assigned_to=:a,updated_at=clock_timestamp() WHERE id=:id"),
            {"a": body.assigned_to, "id": ident},
        )
        if body.assigned_to:
            await notify(
                session,
                str(body.assigned_to),
                f"support:assignment:{uuid.uuid4()}",
                "support",
                f"Вам назначено обращение №{t['number']}",
                t["title"],
            )
    audit(session, principal.user_id, "support.change", str(ident), body.model_dump(mode="json", exclude_unset=True))
    await session.commit()
    return await detail(session, ident, principal, admin)


@router.post("/support/tickets/{ident}/read")
@router.post("/admin/support/tickets/{ident}/read")
async def mark_read(
    ident: uuid.UUID,
    body: Read,
    request: Request,
    principal: Principal = Depends(interactive),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    await ticket(session, ident, principal, "/admin/" in request.url.path)
    maximum = await session.scalar(
        text("SELECT COALESCE(max(id),0) FROM auth.support_messages WHERE ticket_id=:id"), {"id": ident}
    )
    await session.execute(
        text(
            "INSERT INTO auth.support_reads(ticket_id,user_id,last_message_id) VALUES(:t,:u,:m) "
            "ON CONFLICT(ticket_id,user_id) DO UPDATE SET last_message_id=GREATEST(auth.support_reads.last_message_id,EXCLUDED.last_message_id),updated_at=now()"
        ),
        {"t": ident, "u": uuid.UUID(principal.user_id), "m": min(body.through_id, int(maximum))},
    )
    await session.commit()
    return {"ok": True}


@router.get("/admin/support/staff")
async def operators(
    principal: Principal = Depends(interactive), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    await staff(session, principal)
    return await rows(
        session,
        "SELECT u.id,u.display_name FROM auth.application_users u "
        "WHERE u.status='active' AND EXISTS(SELECT 1 FROM auth.effective_permissions p WHERE p.user_id=u.id AND p.permission='admin.support.read') "
        "AND EXISTS(SELECT 1 FROM auth.effective_permissions p WHERE p.user_id=u.id AND p.permission='admin.support.reply') ORDER BY u.display_name LIMIT 300",
    )


@router.websocket("/support/live")
async def live(
    websocket: WebSocket, session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)
) -> None:
    if websocket.headers.get("origin") not in settings.cors_origin_list:
        await websocket.close(code=1008)
        return
    request = Request({**websocket.scope, "type": "http", "method": "GET"})
    previous, accepted, ticks = None, False, 0
    try:
        while True:
            principal = await current_principal(request, Response(), session, settings)
            await interactive(principal)
            admin = websocket.query_params.get("admin") == "1"
            if admin:
                await staff(session, principal)
            revision = await session.scalar(
                text("SELECT COALESCE(max(updated_at)::text,'') FROM auth.support_tickets WHERE :admin OR user_id=:u"),
                {"admin": admin, "u": uuid.UUID(principal.user_id)},
            )
            value = await unread(principal, session)
            signature = (revision, value["unread"])
            await session.commit()
            if not accepted:
                await websocket.accept()
                accepted = True
            if signature != previous:
                await websocket.send_json({"event": "support.changed", **value})
                previous = signature
            elif ticks % 10 == 0:
                await websocket.send_json({"event": "ping"})
            ticks += 1
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=2)
                await asyncio.sleep(2)
            except TimeoutError:
                pass
            except KeyError:
                await websocket.close(code=1003)
                return
    except HTTPException:
        await websocket.close(code=1008)
    except WebSocketDisconnect:
        pass

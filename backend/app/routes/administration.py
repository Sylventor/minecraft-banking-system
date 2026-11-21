from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..experience import notify
from ..models import AuditEvent
from ..rbac import administration_lock, protect_last_superadmin
from ..schemas import StrictModel
from ..security import Principal, require_admin

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class InvestigationCreate(StrictModel):
    subject_type: Literal["user", "account", "operation", "api_key"]
    subject_id: uuid.UUID
    note: Annotated[str, Field(min_length=1, max_length=4000)]


class InvestigationNote(StrictModel):
    body: Annotated[str, Field(min_length=1, max_length=4000)]


class UserState(StrictModel):
    status: Literal["active", "blocked", "archived"]
    reason: Annotated[str, Field(min_length=1, max_length=500)]


@router.get("/audit")
async def audit_events(
    limit: int = Query(50, ge=1, le=100),
    cursor: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    action: str = Query("", max_length=100),
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    principal: Principal = Depends(require_admin("admin.audit")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id,actor_id,action,target_type,target_id,metadata_json,created_at FROM auth.audit_events "
                    "WHERE (CAST(:actor AS uuid) IS NULL OR actor_id=:actor) AND (:action='' OR position(:action in action)>0) "
                    "AND (CAST(:from_date AS timestamptz) IS NULL OR created_at>=:from_date) AND (CAST(:to_date AS timestamptz) IS NULL OR created_at<:to_date) "
                    "AND (CAST(:cursor AS uuid) IS NULL OR (created_at,id)<(SELECT created_at,id FROM auth.audit_events WHERE id=CAST(:cursor AS uuid))) "
                    "ORDER BY created_at DESC,id DESC LIMIT :limit"
                ),
                {
                    "cursor": str(cursor) if cursor else None,
                    "limit": limit + 1,
                    "actor": actor_id,
                    "action": action,
                    "from_date": from_date,
                    "to_date": to_date,
                },
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [dict(row) for row in rows[:limit]],
        "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
    }


@router.get("/users")
async def users(
    query: str = Query("", max_length=100),
    limit: int = Query(50, ge=1, le=100),
    cursor: uuid.UUID | None = None,
    principal: Principal = Depends(require_admin("admin.users.read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT u.id,u.status,u.display_name,u.bank_username,u.created_at,e.external_id discord_id,m.nickname minecraft_nickname "
                    "FROM auth.application_users u LEFT JOIN auth.external_identities e ON e.user_id=u.id AND e.provider='discord' "
                    "LEFT JOIN auth.minecraft_verified_links m ON m.user_id=u.id "
                    "WHERE (:query='' OR position(lower(:query) in lower(u.display_name||' '||COALESCE(u.bank_username,'')||' '||COALESCE(m.nickname,'')))>0 OR e.external_id=:query OR u.id::text=:query) "
                    "AND (CAST(:cursor AS uuid) IS NULL OR u.id>:cursor) ORDER BY u.id LIMIT :limit"
                ),
                {"query": query.strip(), "cursor": cursor, "limit": limit + 1},
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [dict(row) for row in rows[:limit]],
        "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
    }


@router.post("/users/{user_id}/state")
async def user_state(
    user_id: uuid.UUID,
    body: UserState,
    principal: Principal = Depends(require_admin("admin.users")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    await administration_lock(session, principal.user_id, "admin.users")
    if str(user_id) == principal.user_id:
        raise HTTPException(409, "cannot change your own administrative profile state")
    if body.status != "active":
        await protect_last_superadmin(session, user_id)
    row = (
        await session.execute(
            text("UPDATE auth.application_users SET status=:status WHERE id=:id RETURNING id"),
            {"status": body.status, "id": user_id},
        )
    ).first()
    if row is None:
        raise HTTPException(404, "user not found")
    session.add(
        AuditEvent(
            actor_id=uuid.UUID(principal.user_id),
            action="user.state",
            target_type="user",
            target_id=str(user_id),
            metadata_json={"status": body.status, "reason": body.reason},
        )
    )
    await notify(
        session,
        str(user_id),
        "user-state:" + str(uuid.uuid4()),
        "security",
        "Статус профиля изменён",
        "Ваш профиль активен."
        if body.status == "active"
        else "Доступ к вашему профилю ограничен. Обратитесь к администрации банка.",
    )
    await session.commit()
    return {"id": str(user_id), "status": body.status}


@router.get("/investigations")
async def investigations(
    limit: int = Query(50, ge=1, le=100),
    cursor: uuid.UUID | None = None,
    paginated: bool = False,
    principal: Principal = Depends(require_admin("admin.investigations.read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object] | list[dict[str, object]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT * FROM auth.investigations WHERE (CAST(:cursor AS uuid) IS NULL OR (created_at,id)<(SELECT created_at,id FROM auth.investigations WHERE id=:cursor)) ORDER BY created_at DESC,id DESC LIMIT :limit"
                ),
                {"limit": limit + 1, "cursor": cursor},
            )
        )
        .mappings()
        .all()
    )
    items = [dict(row) for row in rows[:limit]]
    return (
        {"items": items, "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None}
        if paginated
        else items
    )


@router.post("/investigations", status_code=201)
async def create_investigation(
    body: InvestigationCreate,
    principal: Principal = Depends(require_admin("admin.investigations")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    ident = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO auth.investigations(id,status,subject_type,subject_id,created_by) VALUES(:id,'open',:type,:subject,:actor)"
        ),
        {
            "id": ident,
            "type": body.subject_type,
            "subject": str(body.subject_id),
            "actor": uuid.UUID(principal.user_id),
        },
    )
    await session.execute(
        text(
            "INSERT INTO auth.investigation_notes(id,investigation_id,author_id,body) VALUES(:id,:investigation,:author,:body)"
        ),
        {"id": uuid.uuid4(), "investigation": ident, "author": uuid.UUID(principal.user_id), "body": body.note},
    )
    session.add(
        AuditEvent(
            actor_id=uuid.UUID(principal.user_id),
            action="investigation.create",
            target_type="investigation",
            target_id=str(ident),
            metadata_json={"subject_type": body.subject_type, "subject_id": str(body.subject_id)},
        )
    )
    await session.commit()
    return {"id": str(ident), "status": "open"}


@router.get("/investigations/{investigation_id}/notes")
async def notes(
    investigation_id: uuid.UUID,
    principal: Principal = Depends(require_admin("admin.investigations.read")),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id,author_id,body,created_at FROM auth.investigation_notes WHERE investigation_id=:id ORDER BY created_at,id"
                ),
                {"id": investigation_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


@router.post("/investigations/{investigation_id}/notes", status_code=201)
async def add_note(
    investigation_id: uuid.UUID,
    body: InvestigationNote,
    principal: Principal = Depends(require_admin("admin.investigations")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    row = (
        await session.execute(
            text("SELECT status FROM auth.investigations WHERE id=:id FOR UPDATE"), {"id": investigation_id}
        )
    ).first()
    if row is None:
        raise HTTPException(404, "investigation not found")
    if row[0] != "open":
        raise HTTPException(409, "investigation is closed")
    ident = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO auth.investigation_notes(id,investigation_id,author_id,body) VALUES(:id,:investigation,:author,:body)"
        ),
        {"id": ident, "investigation": investigation_id, "author": uuid.UUID(principal.user_id), "body": body.body},
    )
    session.add(
        AuditEvent(
            actor_id=uuid.UUID(principal.user_id),
            action="investigation.note",
            target_type="investigation",
            target_id=str(investigation_id),
            metadata_json={"note_id": str(ident)},
        )
    )
    await session.commit()
    return {"id": str(ident)}


@router.post("/investigations/{investigation_id}/close")
async def close_investigation(
    investigation_id: uuid.UUID,
    principal: Principal = Depends(require_admin("admin.investigations")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    row = (
        await session.execute(
            text(
                "UPDATE auth.investigations SET status='closed',closed_at=now() WHERE id=:id AND status='open' RETURNING id"
            ),
            {"id": investigation_id},
        )
    ).first()
    if row is None:
        raise HTTPException(409, "investigation not found or already closed")
    session.add(
        AuditEvent(
            actor_id=uuid.UUID(principal.user_id),
            action="investigation.close",
            target_type="investigation",
            target_id=str(investigation_id),
            metadata_json={},
        )
    )
    await session.commit()
    return {"id": str(investigation_id), "status": "closed"}

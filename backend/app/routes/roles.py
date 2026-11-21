from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..experience import audit, notify, rows
from ..rbac import administration_lock, permissions, protect_last_superadmin
from ..schemas import StrictModel
from ..security import Principal, require_admin

router = APIRouter(prefix="/api/v1/admin", tags=["administrative roles"])


class RoleInput(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    description: Annotated[str, Field(max_length=500)] = ""
    permissions: Annotated[list[str], Field(max_length=50)]


class Assignment(StrictModel):
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    confirmed: bool


@router.get("/permissions")
async def registry(
    principal: Principal = Depends(require_admin("admin.roles.manage")), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    return await rows(session, "SELECT * FROM auth.permission_registry ORDER BY permission")


@router.get("/roles")
async def roles(
    q: str = Query("", max_length=80),
    offset: int = Query(0, ge=0, le=1000000),
    limit: int = Query(25, ge=1, le=100),
    principal: Principal = Depends(require_admin("admin.roles.manage")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    found = await rows(
        session,
        """SELECT r.*,ARRAY(SELECT permission FROM auth.role_permissions WHERE role_id=r.id ORDER BY permission) permissions,
      (SELECT count(*) FROM auth.user_roles WHERE role_id=r.id AND revoked_at IS NULL) members
      FROM auth.admin_roles r WHERE position(lower(:q) in lower(name))>0 ORDER BY system_key NULLS LAST,name LIMIT :limit OFFSET :offset""",
        {"q": q, "limit": limit + 1, "offset": offset},
    )
    return {"items": found[:limit], "next_offset": offset + limit if len(found) > limit else None}


async def validate_role(session: AsyncSession, body: RoleInput, allowed: set[str]) -> None:
    registry = set((await session.scalars(text("SELECT permission FROM auth.permission_registry"))).all())
    if not body.name.strip() or not set(body.permissions) <= registry:
        raise HTTPException(422, "Неизвестное разрешение или пустое название")
    if not set(body.permissions) <= allowed:
        raise HTTPException(403, "Нельзя выдавать полномочия, которых у вас нет")


@router.post("/roles", status_code=201)
async def create_role(
    body: RoleInput,
    principal: Principal = Depends(require_admin("admin.roles.manage")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    allowed = await administration_lock(session, principal.user_id, "admin.roles.manage")
    await validate_role(session, body, allowed)
    ident = uuid.uuid4()
    await session.execute(
        text("INSERT INTO auth.admin_roles(id,name,description,created_by) VALUES(:id,:name,:description,:actor)"),
        {
            "id": ident,
            "name": body.name.strip(),
            "description": body.description,
            "actor": uuid.UUID(principal.user_id),
        },
    )
    for p in set(body.permissions):
        await session.execute(text("INSERT INTO auth.role_permissions VALUES(:role,:p)"), {"role": ident, "p": p})
    audit(session, principal.user_id, "role.create", str(ident), body.model_dump())
    await session.commit()
    return {"id": ident, **body.model_dump()}


@router.patch("/roles/{role_id}")
async def change_role(
    role_id: uuid.UUID,
    body: RoleInput,
    principal: Principal = Depends(require_admin("admin.roles.manage")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    allowed = await administration_lock(session, principal.user_id, "admin.roles.manage")
    existing = await rows(session, "SELECT * FROM auth.admin_roles WHERE id=:id FOR UPDATE", {"id": role_id})
    if not existing:
        raise HTTPException(404, "Роль не найдена")
    if existing[0]["system_key"]:
        raise HTTPException(409, "Системный шаблон защищён. Создайте пользовательскую роль")
    members = await rows(
        session, "SELECT user_id FROM auth.user_roles WHERE role_id=:id AND revoked_at IS NULL", {"id": role_id}
    )
    if any(str(m["user_id"]) == principal.user_id for m in members):
        raise HTTPException(403, "Нельзя изменять собственную роль")
    old = set(
        (
            await session.scalars(
                text("SELECT permission FROM auth.role_permissions WHERE role_id=:id"), {"id": role_id}
            )
        ).all()
    )
    if not old <= allowed:
        raise HTTPException(403, "Полномочия этой роли превышают ваши")
    await validate_role(session, body, allowed)
    await session.execute(
        text("UPDATE auth.admin_roles SET name=:name,description=:description WHERE id=:id"),
        {"id": role_id, "name": body.name.strip(), "description": body.description},
    )
    await session.execute(text("DELETE FROM auth.role_permissions WHERE role_id=:id"), {"id": role_id})
    for p in set(body.permissions):
        await session.execute(text("INSERT INTO auth.role_permissions VALUES(:role,:p)"), {"role": role_id, "p": p})
    for member in members:
        await notify(
            session,
            str(member["user_id"]),
            "role:" + str(uuid.uuid4()),
            "security",
            "Права изменены",
            "Администратор изменил вашу административную роль.",
        )
    audit(session, principal.user_id, "role.update", str(role_id), {"before": sorted(old), **body.model_dump()})
    await session.commit()
    return {"id": role_id, **body.model_dump()}


@router.get("/users/{user_id}/permissions")
async def effective(
    user_id: uuid.UUID,
    principal: Principal = Depends(require_admin("admin.users.read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if not await session.scalar(text("SELECT id FROM auth.application_users WHERE id=:id"), {"id": user_id}):
        raise HTTPException(404, "Пользователь не найден")
    return {
        "permissions": sorted(await permissions(session, user_id)),
        "legacy_permissions": await rows(
            session,
            "SELECT permission,granted_by,reason FROM auth.user_permissions WHERE user_id=:id AND revoked_at IS NULL",
            {"id": user_id},
        ),
        "roles": await rows(
            session,
            "SELECT r.* FROM auth.admin_roles r JOIN auth.user_roles u ON u.role_id=r.id WHERE u.user_id=:id AND u.revoked_at IS NULL ORDER BY r.name",
            {"id": user_id},
        ),
    }


@router.put("/users/{user_id}/roles/{role_id}")
async def assign(
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    body: Assignment,
    principal: Principal = Depends(require_admin("admin.roles.manage")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await set_assignment(session, principal, user_id, role_id, body, False)


@router.post("/users/{user_id}/roles/{role_id}/revoke")
async def revoke(
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    body: Assignment,
    principal: Principal = Depends(require_admin("admin.roles.manage")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await set_assignment(session, principal, user_id, role_id, body, True)


async def set_assignment(
    session: AsyncSession, actor: Principal, user: uuid.UUID, role: uuid.UUID, body: Assignment, revoke: bool
) -> dict[str, Any]:
    allowed = await administration_lock(session, actor.user_id, "admin.roles.manage")
    if actor.user_id == str(user):
        raise HTTPException(403, "Нельзя изменять собственные административные права")
    if not body.confirmed or not body.reason.strip():
        raise HTTPException(422, "Подтвердите действие и укажите причину")
    found = await rows(session, "SELECT * FROM auth.admin_roles WHERE id=:id", {"id": role})
    if not found or not await session.scalar(text("SELECT id FROM auth.application_users WHERE id=:id"), {"id": user}):
        raise HTTPException(404, "Роль или пользователь не найдены")
    requested = set(
        (
            await session.scalars(
                text("SELECT permission FROM auth.role_permissions WHERE role_id=:role"), {"role": role}
            )
        ).all()
    )
    if not requested <= allowed:
        raise HTTPException(403, "Нельзя управлять полномочиями, которых у вас нет")
    if revoke and found[0]["system_key"] == "superadmin":
        await protect_last_superadmin(session, user)
    if revoke:
        await session.execute(
            text(
                "UPDATE auth.user_roles SET revoked_at=now() WHERE user_id=:user AND role_id=:role AND revoked_at IS NULL"
            ),
            {"user": user, "role": role},
        )
    else:
        await session.execute(
            text("""INSERT INTO auth.user_roles(user_id,role_id,granted_by,reason) VALUES(:user,:role,:actor,:reason)
          ON CONFLICT(user_id,role_id) DO UPDATE SET revoked_at=NULL,granted_by=EXCLUDED.granted_by,reason=EXCLUDED.reason,created_at=now()"""),
            {"user": user, "role": role, "actor": uuid.UUID(actor.user_id), "reason": body.reason},
        )
    audit(
        session,
        actor.user_id,
        "role.revoke" if revoke else "role.assign",
        str(user),
        {"role_id": str(role), "reason": body.reason},
    )
    await notify(
        session,
        str(user),
        "role:" + str(uuid.uuid4()),
        "security",
        "Права изменены",
        "Администратор обновил ваши права доступа к банку.",
    )
    await session.commit()
    return {"permissions": sorted(await permissions(session, user))}


@router.get("/roles/{role_id}/users")
async def role_users(
    role_id: uuid.UUID,
    offset: int = Query(0, ge=0, le=1000000),
    limit: int = Query(25, ge=1, le=100),
    principal: Principal = Depends(require_admin("admin.roles.manage")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    found = await rows(
        session,
        """SELECT p.id,p.bank_username,p.display_name,p.status FROM auth.application_users p
      JOIN auth.user_roles u ON u.user_id=p.id WHERE u.role_id=:role AND u.revoked_at IS NULL ORDER BY p.id LIMIT :limit OFFSET :offset""",
        {"role": role_id, "offset": offset, "limit": limit + 1},
    )
    return {"items": found[:limit], "next_offset": offset + limit if len(found) > limit else None}

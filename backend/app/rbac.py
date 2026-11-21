"""One effective permission model: legacy grants UNION explicitly configured roles."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def permissions(session: AsyncSession, user: str | uuid.UUID) -> set[str]:
    found = set(
        (
            await session.scalars(
                text("SELECT permission FROM auth.effective_permissions WHERE user_id=:user"),
                {"user": uuid.UUID(str(user))},
            )
        ).all()
    )
    # Old 2.1 permissions retain their earlier capabilities.
    if "admin.users" in found:
        found.add("admin.users.read")
    if "admin.investigations" in found:
        found.add("admin.investigations.read")
    return found


async def administration_lock(session: AsyncSession, actor: str, permission: str) -> set[str]:
    await session.execute(text("SELECT pg_advisory_xact_lock(220022)"))
    active = await session.scalar(
        text("SELECT status='active' FROM auth.application_users WHERE id=:id"), {"id": uuid.UUID(actor)}
    )
    allowed = await permissions(session, actor)
    if not active or permission not in allowed:
        raise HTTPException(403, "Административные полномочия отозваны")
    return allowed


async def protect_last_superadmin(session: AsyncSession, user: uuid.UUID) -> None:
    last = await session.scalar(
        text("""
      SELECT EXISTS(SELECT 1 FROM auth.user_roles u JOIN auth.admin_roles r ON r.id=u.role_id
        WHERE u.user_id=:user AND u.revoked_at IS NULL AND r.system_key='superadmin')
      AND NOT EXISTS(SELECT 1 FROM auth.user_roles u JOIN auth.admin_roles r ON r.id=u.role_id
        JOIN auth.application_users p ON p.id=u.user_id WHERE p.status='active' AND u.revoked_at IS NULL
        AND r.system_key='superadmin' AND u.user_id<>:user)
    """),
        {"user": user},
    )
    if last:
        raise HTTPException(409, "Нельзя лишить доступа последнего активного Superadmin")

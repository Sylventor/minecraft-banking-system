from __future__ import annotations

import html
import re
import uuid
from typing import Annotated, Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..experience import audit, rows
from ..rbac import administration_lock
from ..schemas import StrictModel
from ..security import Principal, require_admin

router = APIRouter(prefix="/api/v1", tags=["terms"])
MAX_MARKDOWN_BYTES = 131072


class TermsUpload(StrictModel):
    filename: Annotated[str, Field(min_length=4, max_length=100)]
    markdown: Annotated[str, Field(min_length=1, max_length=MAX_MARKDOWN_BYTES)]
    confirmed: bool


class Confirmation(StrictModel):
    confirmed: bool


def validate_markdown(filename: str, content: str) -> str:
    if not filename.lower().endswith(".md") or any(c in filename for c in "/\\\x00"):
        raise HTTPException(422, "Загрузите текстовый файл с расширением .md")
    try:
        size = len(content.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise HTTPException(422, "Файл должен содержать корректный UTF-8") from error
    if not content.strip() or size > MAX_MARKDOWN_BYTES:
        raise HTTPException(422, "Размер Markdown: от 1 байта до 128 КиБ")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in content):
        raise HTTPException(422, "Файл содержит недопустимые управляющие символы")
    normalized = content
    for _ in range(4):
        normalized = html.unescape(unquote(normalized))
    if re.search(r"<\s*(?:/?[a-zA-Z][^>]*|!--|!doctype|\?)>", normalized, re.I):
        raise HTTPException(422, "HTML в правилах запрещён; используйте Markdown")
    compact = re.sub(r"[\s\\]+", "", normalized).casefold()
    if re.search(r"(?:javascript|vbscript|data|file|blob):", compact):
        raise HTTPException(422, "В правилах обнаружена опасная URL-схема")
    return content.replace("\r\n", "\n")


@router.get("/terms")
async def public_terms(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    found = await rows(
        session, "SELECT id,version,markdown,published_at FROM auth.terms_versions WHERE status='published'"
    )
    return {"current": found[0] if found else None}


@router.get("/admin/terms")
async def versions(
    offset: int = Query(0, ge=0, le=1000000),
    limit: int = Query(20, ge=1, le=100),
    principal: Principal = Depends(require_admin("admin.terms.manage")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    found = await rows(
        session,
        """SELECT v.id,v.version,v.status,v.author_id,u.bank_username author,v.published_at,v.restored_from
      FROM auth.terms_versions v JOIN auth.application_users u ON u.id=v.author_id ORDER BY v.version DESC LIMIT :limit OFFSET :offset""",
        {"limit": limit + 1, "offset": offset},
    )
    return {"items": found[:limit], "next_offset": offset + limit if len(found) > limit else None}


@router.get("/admin/terms/{version_id}")
async def version(
    version_id: uuid.UUID,
    principal: Principal = Depends(require_admin("admin.terms.manage")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    found = await rows(session, "SELECT * FROM auth.terms_versions WHERE id=:id", {"id": version_id})
    if not found:
        raise HTTPException(404, "Версия правил не найдена")
    return found[0]


async def publish(
    session: AsyncSession, principal: Principal, content: str, restored: uuid.UUID | None = None
) -> dict[str, Any]:
    await session.execute(text("SELECT pg_advisory_xact_lock(220023)"))
    await session.execute(text("UPDATE auth.terms_versions SET status='archived' WHERE status='published'"))
    row = (
        await rows(
            session,
            """INSERT INTO auth.terms_versions(markdown,author_id,status,restored_from)
      VALUES(:markdown,:author,'published',:restored) RETURNING *""",
            {"markdown": content, "author": uuid.UUID(principal.user_id), "restored": restored},
        )
    )[0]
    audit(
        session,
        principal.user_id,
        "terms.restore" if restored else "terms.publish",
        str(row["id"]),
        {"version": row["version"], "restored_from": str(restored) if restored else None},
    )
    await session.commit()
    return row


@router.post("/admin/terms", status_code=201)
async def upload(
    body: TermsUpload,
    principal: Principal = Depends(require_admin("admin.terms.manage")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    content = validate_markdown(body.filename, body.markdown)
    if not body.confirmed:
        raise HTTPException(422, "Подтвердите публикацию правил")
    await administration_lock(session, principal.user_id, "admin.terms.manage")
    return await publish(session, principal, content)


@router.post("/admin/terms/{version_id}/restore", status_code=201)
async def restore(
    version_id: uuid.UUID,
    body: Confirmation,
    principal: Principal = Depends(require_admin("admin.terms.manage")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if not body.confirmed:
        raise HTTPException(422, "Подтвердите восстановление версии")
    await administration_lock(session, principal.user_id, "admin.terms.manage")
    found = await rows(session, "SELECT markdown FROM auth.terms_versions WHERE id=:id", {"id": version_id})
    if not found:
        raise HTTPException(404, "Версия правил не найдена")
    return await publish(session, principal, validate_markdown("rules.md", found[0]["markdown"]), version_id)


@router.post("/admin/terms/unpublish")
async def unpublish(
    body: Confirmation,
    principal: Principal = Depends(require_admin("admin.terms.manage")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    if not body.confirmed:
        raise HTTPException(422, "Подтвердите снятие правил с публикации")
    await administration_lock(session, principal.user_id, "admin.terms.manage")
    await session.execute(text("SELECT pg_advisory_xact_lock(220023)"))
    await session.execute(text("UPDATE auth.terms_versions SET status='unpublished' WHERE status='published'"))
    audit(session, principal.user_id, "terms.unpublish", "current")
    await session.commit()
    return {"unpublished": True}

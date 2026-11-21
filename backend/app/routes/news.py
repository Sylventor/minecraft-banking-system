"""Public official news; publishing uses the existing session, RBAC and administrative lock."""

import re
import uuid
from datetime import datetime
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..experience import audit, rows
from ..rbac import administration_lock
from ..schemas import StrictModel
from ..security import Principal, require_admin
from .terms import validate_markdown

router = APIRouter(prefix="/api/v1", tags=["News"])
Category = Literal["general", "updates", "maintenance", "important", "rules", "exchange"]
Status = Literal["draft", "published"]
PERMISSION = "admin.news.manage"
COLUMNS = """n.id,n.title,n.slug,n.summary,n.content,n.category,n.cover_image,n.status,n.is_pinned,
    n.created_at,n.updated_at,n.published_at,
    COALESCE(NULLIF(u.display_name,''),NULLIF(u.bank_username,''),'Администрация Banking System') AS author"""
JOIN = " FROM auth.news n JOIN auth.application_users u ON u.id=n.author_id "


def cover_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    if any(ord(c) <= 32 for c in value) or "\\" in value:
        raise ValueError("Некорректная ссылка обложки")
    parsed = urlsplit(value)
    if parsed.username or parsed.password:
        raise ValueError("Ссылка обложки не должна содержать учётные данные")
    if parsed.scheme == "https" and parsed.hostname:
        return value
    if not parsed.scheme and not parsed.netloc and value.startswith(("/images/", "/branding/", "/news-covers/")):
        if any(part in {".", ".."} for part in unquote(parsed.path).split("/")) or "\\" in unquote(parsed.path):
            raise ValueError("Некорректный путь обложки")
        return value
    raise ValueError("Обложка: HTTPS-ссылка или путь /images/, /branding/, /news-covers/")


class NewsInput(StrictModel):
    title: str = Field(min_length=1, max_length=180)
    summary: str = Field(default="", max_length=500)
    content: str = Field(min_length=1, max_length=131072)
    category: Category = "general"
    cover_image: str | None = Field(default=None, max_length=2048)
    status: Status = "draft"
    is_pinned: bool = Field(default=False, strict=True)

    @field_validator("title", "summary")
    @classmethod
    def clean_text(cls, value: str, info: Any) -> str:
        value = value.strip()
        if (info.field_name == "title" and not value) or any(ord(c) < 32 for c in value):
            raise ValueError("Укажите текст без управляющих символов")
        return value

    @field_validator("content")
    @classmethod
    def markdown(cls, value: str) -> str:
        try:
            return validate_markdown("news.md", value)
        except HTTPException as error:
            raise ValueError("Текст новости: до 128 КиБ, без HTML и опасных URL-схем") from error

    @field_validator("cover_image")
    @classmethod
    def cover(cls, value: str | None) -> str | None:
        return cover_url(value)


class NewsAction(StrictModel):
    expected_updated_at: datetime
    confirmed: Literal[True]


class NewsPatch(StrictModel):
    expected_updated_at: datetime
    title: str | None = Field(default=None, min_length=1, max_length=180)
    summary: str | None = Field(default=None, max_length=500)
    content: str | None = Field(default=None, min_length=1, max_length=131072)
    category: Category | None = None
    cover_image: str | None = Field(default=None, max_length=2048)
    status: Status | None = None
    is_pinned: bool | None = Field(default=None, strict=True)


class NewsItem(StrictModel):
    id: uuid.UUID
    title: str
    slug: str
    summary: str
    content: str
    category: Category
    cover_image: str | None
    author: str
    status: Status
    is_pinned: bool
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None


class NewsCard(StrictModel):
    id: uuid.UUID
    title: str
    slug: str
    summary: str
    category: Category
    cover_image: str | None
    author: str
    status: Status
    is_pinned: bool
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None


class NewsPage(StrictModel):
    items: list[NewsCard]
    next_offset: int | None


def slug_for(title: str, ident: uuid.UUID) -> str:
    alphabet = dict(
        zip(
            "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
            (
                "a",
                "b",
                "v",
                "g",
                "d",
                "e",
                "yo",
                "zh",
                "z",
                "i",
                "y",
                "k",
                "l",
                "m",
                "n",
                "o",
                "p",
                "r",
                "s",
                "t",
                "u",
                "f",
                "kh",
                "ts",
                "ch",
                "sh",
                "shch",
                "",
                "y",
                "",
                "e",
                "yu",
                "ya",
            ),
            strict=True,
        )
    )
    latin = "".join(alphabet.get(char, char) for char in title.lower())
    base = re.sub(r"[^a-z0-9]+", "-", latin).strip("-")[:130].rstrip("-") or "novost"
    return base + "-" + ident.hex


async def item(session: AsyncSession, ident: uuid.UUID, *, lock: bool = False) -> dict[str, Any]:
    found = await rows(
        session,
        "SELECT " + COLUMNS + JOIN + "WHERE n.id=:id AND n.deleted_at IS NULL" + (" FOR UPDATE OF n" if lock else ""),
        {"id": ident},
    )
    if not found:
        raise HTTPException(404, "Новость не найдена")
    return found[0]


def check_version(row: dict[str, Any], expected: datetime) -> None:
    if expected.utcoffset() is None:
        raise HTTPException(422, "Укажите время изменения с часовым поясом")
    if row["updated_at"] != expected:
        raise HTTPException(409, "Новость уже изменена другим редактором. Обновите список перед сохранением.")


@router.get("/news", response_model=NewsPage)
async def public_news(
    category: Category | None = None,
    offset: int = Query(0, ge=0, le=1000000),
    limit: int = Query(12, ge=1, le=30),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    found = await rows(
        session,
        "SELECT "
        + COLUMNS.replace("n.content,", "")
        + JOIN
        + """
      WHERE n.status='published' AND n.deleted_at IS NULL AND (CAST(:category AS text) IS NULL OR n.category=:category)
      ORDER BY n.is_pinned DESC,n.published_at DESC,n.id DESC LIMIT :limit OFFSET :offset
    """,
        {"category": category, "offset": offset, "limit": limit + 1},
    )
    return {"items": found[:limit], "next_offset": offset + limit if len(found) > limit else None}


@router.get("/news/{slug}", response_model=NewsItem)
async def public_article(slug: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or len(slug) > 180:
        raise HTTPException(404, "Новость не найдена")
    found = await rows(
        session,
        "SELECT " + COLUMNS + JOIN + "WHERE n.slug=:slug AND n.status='published' AND n.deleted_at IS NULL",
        {"slug": slug},
    )
    if not found:
        raise HTTPException(404, "Новость не найдена")
    return found[0]


@router.get("/admin/news", response_model=NewsPage)
async def admin_news(
    category: Category | None = None,
    status: Status | None = None,
    offset: int = Query(0, ge=0, le=1000000),
    limit: int = Query(20, ge=1, le=30),
    principal: Principal = Depends(require_admin(PERMISSION)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    found = await rows(
        session,
        "SELECT "
        + COLUMNS.replace("n.content,", "")
        + JOIN
        + """
      WHERE n.deleted_at IS NULL AND (CAST(:category AS text) IS NULL OR n.category=:category)
      AND (CAST(:status AS text) IS NULL OR n.status=:status)
      ORDER BY n.updated_at DESC,n.id DESC LIMIT :limit OFFSET :offset
    """,
        {"category": category, "status": status, "offset": offset, "limit": limit + 1},
    )
    return {"items": found[:limit], "next_offset": offset + limit if len(found) > limit else None}


@router.get("/admin/news/{news_id}", response_model=NewsItem)
async def admin_article(
    news_id: uuid.UUID,
    principal: Principal = Depends(require_admin(PERMISSION)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await item(session, news_id)


@router.post("/admin/news", status_code=201, response_model=NewsItem)
async def create(
    body: NewsInput,
    principal: Principal = Depends(require_admin(PERMISSION)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await administration_lock(session, principal.user_id, PERMISSION)
    ident = uuid.uuid4()
    await session.execute(
        text("""INSERT INTO auth.news(id,title,slug,summary,content,category,cover_image,author_id,status,is_pinned,published_at)
      VALUES(:id,:title,:slug,:summary,:content,:category,:cover_image,:author,:status,:is_pinned,
        CASE WHEN :status='published' THEN clock_timestamp() ELSE NULL END)"""),
        {**body.model_dump(), "id": ident, "slug": slug_for(body.title, ident), "author": uuid.UUID(principal.user_id)},
    )
    audit(session, principal.user_id, "news.create", str(ident), {"status": body.status})
    if body.status == "published":
        audit(session, principal.user_id, "news.publish", str(ident))
    result = await item(session, ident)
    await session.commit()
    return result


async def edit(
    session: AsyncSession, principal: Principal, ident: uuid.UUID, expected: datetime, changes: dict[str, Any]
) -> dict[str, Any]:
    await administration_lock(session, principal.user_id, PERMISSION)
    current = await item(session, ident, lock=True)
    check_version(current, expected)
    if not changes:
        raise HTTPException(422, "Укажите изменения новости")
    fields = NewsInput.model_validate({key: current[key] for key in NewsInput.model_fields} | changes).model_dump()
    if all(current[key] == value for key, value in fields.items()):
        return current
    await session.execute(
        text("""UPDATE auth.news SET title=:title,summary=:summary,content=:content,category=:category,
      cover_image=:cover_image,status=:status,is_pinned=:is_pinned,updated_at=clock_timestamp(),
      published_at=CASE WHEN :status='published' THEN COALESCE(published_at,clock_timestamp()) ELSE published_at END WHERE id=:id"""),
        {**fields, "id": ident},
    )
    action = "news.publish" if fields["status"] == "published" else "news.unpublish"
    audit(
        session,
        principal.user_id,
        action if current["status"] != fields["status"] else "news.edit",
        str(ident),
        {"fields": sorted(changes)},
    )
    result = await item(session, ident)
    await session.commit()
    return result


@router.patch("/admin/news/{news_id}", response_model=NewsItem)
async def patch(
    news_id: uuid.UUID,
    body: NewsPatch,
    principal: Principal = Depends(require_admin(PERMISSION)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True, exclude={"expected_updated_at"})
    try:
        return await edit(session, principal, news_id, body.expected_updated_at, changes)
    except ValueError as error:
        raise HTTPException(422, "Проверьте заголовок, текст и ссылку обложки") from error


@router.post("/admin/news/{news_id}/publish", response_model=NewsItem)
async def publish(
    news_id: uuid.UUID,
    body: NewsAction,
    principal: Principal = Depends(require_admin(PERMISSION)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await edit(session, principal, news_id, body.expected_updated_at, {"status": "published"})


@router.post("/admin/news/{news_id}/unpublish", response_model=NewsItem)
async def unpublish(
    news_id: uuid.UUID,
    body: NewsAction,
    principal: Principal = Depends(require_admin(PERMISSION)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await edit(session, principal, news_id, body.expected_updated_at, {"status": "draft"})


@router.delete("/admin/news/{news_id}", status_code=204)
async def delete(
    news_id: uuid.UUID,
    body: NewsAction,
    principal: Principal = Depends(require_admin(PERMISSION)),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await administration_lock(session, principal.user_id, PERMISSION)
    current = await item(session, news_id, lock=True)
    check_version(current, body.expected_updated_at)
    await session.execute(
        text(
            "UPDATE auth.news SET deleted_at=clock_timestamp(),updated_at=clock_timestamp(),status='draft',is_pinned=false WHERE id=:id"
        ),
        {"id": news_id},
    )
    audit(session, principal.user_id, "news.delete", str(news_id), {"slug": current["slug"]})
    await session.commit()
    return Response(status_code=204)

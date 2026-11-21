"""News use the existing PostgreSQL authentication/RBAC; no financial core or production database."""

import asyncio
import uuid

import pytest
import pytest_asyncio
from test_api_security import api_client as api_fixture
from test_api_security import session_cookie

api_client = api_fixture
ORIGIN = {"Origin": "http://localhost:8080"}


@pytest_asyncio.fixture
async def news_client(api_client):
    client, db, settings = api_client
    # api_client/database enforce a newly bootstrapped horizon_test_* database.
    db.execute("DELETE FROM auth.news")
    user, token = session_cookie(db)
    db.execute(
        "INSERT INTO auth.user_permissions(user_id,permission,granted_by,reason) VALUES(%s,'admin.news.manage',%s,'News test')",
        (user, user),
    )
    db.commit()
    client.cookies.set("horizon_session", token)
    return client, db, settings, user


async def create(client, **changes):
    payload = {
        "title": "Обновление Banking System",
        "summary": "Коротко о важном",
        "content": "# Новая функция\n\n**Подробности** и [правила](/terms).",
        "category": "updates",
        **changes,
    }
    result = await client.post("/api/v1/admin/news", headers=ORIGIN, json=payload)
    assert result.status_code == 201, result.text
    return result.json()


@pytest.mark.asyncio
async def test_public_visibility_stable_slug_publish_unpublish_and_audit(news_client):
    client, db, _, user = news_client
    original_token = client.cookies.get("horizon_session")
    draft = await create(client)
    assert draft["published_at"] is None
    client.cookies.clear()
    assert (await client.get("/api/v1/news")).json()["items"] == []
    assert (await client.get("/api/v1/news/" + draft["slug"])).status_code == 404
    _, token = session_cookie(db)
    # The new ordinary user cannot use the news editor.
    client.cookies.set("horizon_session", token)
    assert (await client.get("/api/v1/admin/news")).status_code == 403
    client.cookies.clear()
    client.cookies.set("horizon_session", original_token)
    published = await client.post(
        f"/api/v1/admin/news/{draft['id']}/publish",
        headers=ORIGIN,
        json={"confirmed": True, "expected_updated_at": draft["updated_at"]},
    )
    assert published.status_code == 200, published.text
    live = published.json()
    renamed = await client.patch(
        f"/api/v1/admin/news/{draft['id']}",
        headers=ORIGIN,
        json={"title": "Новое название", "expected_updated_at": live["updated_at"]},
    )
    assert renamed.status_code == 200, renamed.text
    current = renamed.json()
    assert (
        current["slug"] == draft["slug"]
        and current["created_at"] == draft["created_at"]
        and current["published_at"] == live["published_at"]
    )
    article = await client.get("/api/v1/news/" + draft["slug"])
    assert (
        article.status_code == 200
        and article.json()["content"] == draft["content"]
        and article.json()["author"] == "Test user"
    )
    unpublished = await client.post(
        f"/api/v1/admin/news/{draft['id']}/unpublish",
        headers=ORIGIN,
        json={"confirmed": True, "expected_updated_at": current["updated_at"]},
    )
    assert unpublished.status_code == 200
    assert (await client.get("/api/v1/news/" + draft["slug"])).status_code == 404
    actions = {
        row[0]
        for row in db.execute(
            "SELECT action FROM auth.audit_events WHERE target_id=%s", (draft["id"],)
        )
    }
    assert {"news.create", "news.publish", "news.edit", "news.unpublish"}.issubset(
        actions
    )
    db.commit()


@pytest.mark.asyncio
async def test_pagination_filters_pins_and_unique_slugs(news_client):
    client, _, _, _ = news_client
    first = await create(
        client, status="published", category="maintenance", is_pinned=True
    )
    second = await create(client, status="published", category="maintenance")
    third = await create(client, status="published", category="maintenance")
    await create(client, status="draft", category="maintenance")
    page = (
        await client.get("/api/v1/news", params={"limit": 2, "category": "maintenance"})
    ).json()
    assert [item["id"] for item in page["items"]] == [
        first["id"],
        third["id"],
    ] and page["next_offset"] == 2
    assert "content" not in page["items"][0]
    rest = (
        await client.get(
            "/api/v1/news", params={"limit": 2, "offset": 2, "category": "maintenance"}
        )
    ).json()
    assert [item["id"] for item in rest["items"]] == [second["id"]] and rest[
        "next_offset"
    ] is None
    assert len({first["slug"], second["slug"], third["slug"]}) == 3
    assert (await client.get("/api/v1/news", params={"category": "rules"})).json()[
        "items"
    ] == []
    assert (
        len(
            (await client.get("/api/v1/admin/news", params={"status": "draft"})).json()[
                "items"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_permissions_csrf_and_recent_auth(news_client):
    client, db, settings, user = news_client
    post = await create(client)
    denied = await client.patch(
        f"/api/v1/admin/news/{post['id']}",
        headers={"Origin": "https://evil.test"},
        json={"title": "Нельзя", "expected_updated_at": post["updated_at"]},
    )
    assert denied.status_code == 403
    db.execute(
        "UPDATE auth.user_sessions SET created_at=now()-interval '20 minutes' WHERE user_id=%s",
        (user,),
    )
    db.commit()
    assert (await client.get("/api/v1/admin/news")).status_code == 200
    assert (
        await client.post(
            "/api/v1/admin/news",
            headers=ORIGIN,
            json={"title": "Нет", "content": "Нет"},
        )
    ).status_code == 403
    from app.security import secret_hash

    prefix = uuid.uuid4().hex[:12]
    secret = uuid.uuid4().hex * 2
    db.execute(
        "INSERT INTO auth.api_keys(id,owner_id,prefix,secret_hash,name,scopes,account_ids) VALUES(%s,%s,%s,%s,'News test',%s,%s)",
        (
            uuid.uuid4(),
            user,
            prefix,
            secret_hash(secret, settings),
            ["profile.read"],
            [],
        ),
    )
    db.commit()
    client.cookies.clear()
    assert (
        await client.get(
            "/api/v1/admin/news",
            headers={"Authorization": f"Bearer hbk_{prefix}_{secret}"},
        )
    ).status_code == 403
    assert (await client.get("/api/v1/admin/news")).status_code == 401


@pytest.mark.asyncio
async def test_strict_validation_markdown_and_covers(news_client):
    client, _, _, _ = news_client
    cases = [
        {"title": " "},
        {"category": "unknown"},
        {"content": "<script>alert(1)</script>"},
        {"content": "[ссылка](javascript:alert(1))"},
        {"content": "я" * 70000},
        {"cover_image": "javascript:alert(1)"},
        {"cover_image": "//example.org/a.png"},
        {"cover_image": "/images/../api/v1/auth/discord/login"},
        {"cover_image": "https://user:password@example.org/a.png"},
        {"is_pinned": "true"},
        {"author_id": str(uuid.uuid4())},
    ]
    for change in cases:
        response = await client.post(
            "/api/v1/admin/news",
            headers=ORIGIN,
            json={"title": "Тест", "content": "Обычный текст", **change},
        )
        assert response.status_code == 422, (change.keys(), response.text)
    item = await create(client, cover_image="https://images.example.org/cover.png")
    assert item["cover_image"] == "https://images.example.org/cover.png"
    assert (await client.get("/api/v1/news", params={"limit": 1000})).status_code == 422


@pytest.mark.asyncio
async def test_concurrent_edits_and_delete_require_current_version_and_confirmation(
    news_client,
):
    client, db, _, _ = news_client
    before = db.execute("SELECT count(*) FROM finance.financial_operations").fetchone()[
        0
    ]
    db.commit()
    item = await create(client, status="published")
    results = await asyncio.gather(
        *(
            client.patch(
                f"/api/v1/admin/news/{item['id']}",
                headers=ORIGIN,
                json={"title": title, "expected_updated_at": item["updated_at"]},
            )
            for title in ["Изменение А", "Изменение Б"]
        )
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    current = next(r.json() for r in results if r.status_code == 200)
    denied = await client.request(
        "DELETE",
        f"/api/v1/admin/news/{item['id']}",
        headers=ORIGIN,
        json={"confirmed": False, "expected_updated_at": current["updated_at"]},
    )
    assert denied.status_code == 422
    deleted = await client.request(
        "DELETE",
        f"/api/v1/admin/news/{item['id']}",
        headers=ORIGIN,
        json={"confirmed": True, "expected_updated_at": current["updated_at"]},
    )
    assert deleted.status_code == 204, deleted.text
    assert (await client.get("/api/v1/news/" + item["slug"])).status_code == 404
    assert (await client.get(f"/api/v1/admin/news/{item['id']}")).status_code == 404
    assert db.execute(
        "SELECT deleted_at IS NOT NULL FROM auth.news WHERE id=%s", (item["id"],)
    ).fetchone()[0]
    assert (
        db.execute("SELECT count(*) FROM finance.financial_operations").fetchone()[0]
        == before
    )
    db.commit()


@pytest.mark.asyncio
async def test_public_openapi_and_superadmin_permission(news_client):
    client, db, _, _ = news_client
    schema = (await client.get("/api/openapi.json")).json()
    for path in ("/api/v1/news", "/api/v1/news/{slug}"):
        assert not schema["paths"][path]["get"].get("security")
    assert not any(path.startswith("/api/v1/admin/news") for path in schema["paths"])
    assert (
        db.execute(
            "SELECT count(*) FROM auth.role_permissions p JOIN auth.admin_roles r ON r.id=p.role_id WHERE p.permission='admin.news.manage' AND r.system_key='superadmin'"
        ).fetchone()[0]
        == 1
    )
    db.commit()

"""Persistent opaque cookies survive a fresh FastAPI application using the same DB."""

import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from test_api_security import api_client as api_fixture, session_cookie

api_client = api_fixture
ORIGIN = {"Origin": "http://localhost:8080"}


@pytest.mark.asyncio
async def test_session_persists_across_new_app_and_rolls_idle_expiry(api_client):
    from app.config import get_settings
    from app.database import get_session
    from app.main import create_app

    client, db, settings = api_client
    user, token = session_cookie(db)
    db.execute(
        "UPDATE auth.user_sessions SET last_activity_at=now()-interval '10 minutes',expires_at=now()+interval '1 day' WHERE user_id=%s",
        (user,),
    )
    db.commit()
    client.cookies.set("horizon_session", token)
    first = await client.get("/api/v1/me")
    assert first.status_code == 200, first.text
    assert (
        "HttpOnly" in first.headers["set-cookie"]
        and "SameSite=lax" in first.headers["set-cookie"]
    )
    expires = db.execute(
        "SELECT expires_at FROM auth.user_sessions WHERE user_id=%s", (user,)
    ).fetchone()[0]
    assert expires > datetime.now(UTC) + timedelta(days=29)
    db.commit()
    # Equivalent persistence boundary to replacing the API container image:
    # a new app/process has no session memory or freshly generated signing key.
    restarted = create_app(settings)
    restarted.dependency_overrides[get_session] = (
        client._transport.app.dependency_overrides[get_session]
    )
    restarted.dependency_overrides[get_settings] = lambda: settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted),
        base_url="http://localhost:8080",
        cookies={"horizon_session": token},
    ) as fresh:
        result = await fresh.get("/api/v1/me")
        assert result.status_code == 200 and result.json()["id"] == user


@pytest.mark.asyncio
async def test_refresh_rotates_and_old_token_cannot_be_replayed(api_client):
    client, db, _ = api_client
    user, token = session_cookie(db)
    client.cookies.set("horizon_session", token)
    refreshed = await client.post("/api/v1/auth/session/refresh", headers=ORIGIN)
    assert refreshed.status_code == 204, refreshed.text
    new = client.cookies.get("horizon_session", domain="localhost.local", path="/")
    assert new and new != token
    client.cookies.clear()
    client.cookies.set("horizon_session", new)
    assert (await client.get("/api/v1/me")).status_code == 200
    client.cookies.clear()
    client.cookies.set("horizon_session", token)
    assert (await client.get("/api/v1/me")).status_code == 401
    rows = db.execute(
        "SELECT token_hash,revoked_at,absolute_expires_at FROM auth.user_sessions WHERE user_id=%s ORDER BY revoked_at NULLS FIRST",
        (user,),
    ).fetchall()
    assert all(token not in str(r) and new not in str(r) for r in rows)
    assert rows[0][2] == rows[1][2]
    db.commit()


@pytest.mark.asyncio
async def test_absolute_expiry_and_revoke_other_sessions(api_client):
    client, db, _ = api_client
    user, token = session_cookie(db)
    db.execute(
        "INSERT INTO auth.user_sessions(token_hash,user_id,expires_at) VALUES(%s,%s,now()+interval '1 day')",
        (hashlib.sha256(b"other-session").hexdigest(), user),
    )
    db.commit()
    client.cookies.set("horizon_session", token)
    listed = await client.get("/api/v1/me/sessions")
    assert listed.status_code == 200 and len(listed.json()) == 2
    assert all("token_hash" not in s and "last_activity_at" in s for s in listed.json())
    assert (
        await client.post("/api/v1/me/sessions/revoke-others", headers=ORIGIN)
    ).status_code == 204
    assert len((await client.get("/api/v1/me/sessions")).json()) == 1
    db.execute(
        "UPDATE auth.user_sessions SET absolute_expires_at=now()-interval '1 second' WHERE token_hash=%s",
        (hashlib.sha256(token.encode()).hexdigest(),),
    )
    db.commit()
    assert (await client.get("/api/v1/me")).status_code == 401


@pytest.mark.asyncio
async def test_idle_expiry_recent_auth_for_sensitive_admin_changes_and_logout(
    api_client,
):
    client, db, _ = api_client
    user, token = session_cookie(db)
    db.execute(
        "INSERT INTO auth.user_roles(user_id,role_id,granted_by,reason) SELECT %s,id,%s,'Sensitive auth test' FROM auth.admin_roles WHERE system_key='superadmin'",
        (user, user),
    )
    db.execute(
        "UPDATE auth.user_sessions SET created_at=now()-interval '20 minutes' WHERE user_id=%s",
        (user,),
    )
    db.commit()
    client.cookies.set("horizon_session", token)
    denied = await client.post(
        "/api/v1/admin/roles",
        headers=ORIGIN,
        json={"name": "Recent auth test", "description": "", "permissions": []},
    )
    assert denied.status_code == 403 and "подтвердите вход" in denied.text
    assert (await client.get("/api/v1/me")).status_code == 200
    db.execute(
        "UPDATE auth.user_sessions SET expires_at=now()-interval '1 second' WHERE user_id=%s",
        (user,),
    )
    db.commit()
    assert (await client.get("/api/v1/me")).status_code == 401
    db.execute(
        "UPDATE auth.user_sessions SET absolute_expires_at=now()+interval '1 day',expires_at=now()+interval '1 day' WHERE token_hash=%s",
        (hashlib.sha256(token.encode()).hexdigest(),),
    )
    db.commit()
    assert (await client.get("/api/v1/me")).status_code == 200
    assert (await client.post("/api/v1/auth/logout", headers=ORIGIN)).status_code == 204
    client.cookies.set("horizon_session", token)
    assert (await client.get("/api/v1/me")).status_code == 401

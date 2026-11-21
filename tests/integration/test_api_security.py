from __future__ import annotations

import hashlib
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr
from test_core_finance import TOKEN
from test_core_finance import core as core_fixture
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

for name in (
    "INTERNAL_GRPC_TOKEN",
    "SESSION_SECRET",
    "API_KEY_PEPPER",
    "DISCORD_BOT_INTERNAL_TOKEN",
):
    os.environ[name] = "test-" + name.lower() + "-" * 40
os.environ["ENVIRONMENT"] = "test"
os.environ.setdefault("AUTH_DATABASE_URL", "postgresql+asyncpg://test@localhost/test")
sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "backend/app/grpc/generated")
)
from app.config import Settings, get_settings  # noqa: E402 -- isolate configuration before importing services
from app.database import get_session  # noqa: E402
from app.main import create_app  # noqa: E402
from app.security import secret_hash  # noqa: E402

core = core_fixture


@pytest_asyncio.fixture
async def api_client(database):
    base = os.environ["TEST_DATABASE_URL"]
    auth_url = os.environ.get("TEST_AUTH_DATABASE_URL")
    if auth_url:
        auth_url = auth_url.replace("postgresql://", "postgresql+asyncpg://")
    else:
        from psycopg.conninfo import conninfo_to_dict
        from sqlalchemy.engine import URL

        opts = conninfo_to_dict(base)
        auth_url = URL.create(
            "postgresql+asyncpg",
            username="horizon_auth",
            host=opts["host"],
            port=int(opts.get("port", "5432")),
            database=opts["dbname"],
        ).render_as_string(hide_password=False)
    settings = Settings(
        _env_file=None,
        environment="test",
        auth_database_url=auth_url,
        internal_grpc_token="test-internal-token" + "x" * 32,
        session_secret="test-session-secret" + "x" * 32,
        api_key_pepper="test-api-key-pepper" + "x" * 32,
        discord_bot_internal_token="test-bot-token" + "x" * 32,
        discord_client_id="test-discord-client",
        discord_client_secret="test-discord-secret",
        discord_redirect_uri="http://localhost:8080/api/v1/auth/discord/callback",
        minecraft_verifier_token="test-minecraft-verifier-" + "v" * 32,
    )
    app = create_app(settings)
    engine = create_async_engine(settings.auth_database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_override():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings] = lambda: settings
    # Only the external provider transport is simulated; state, DB and sessions remain real.
    discord_id = str(uuid.uuid4().int >> 64)

    def provider(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth2/token":
            return httpx.Response(
                200, json={"access_token": "test-discord-access-token"}
            )
        return httpx.Response(
            200, json={"id": discord_id, "username": "OAuth test user"}
        )

    app.state.oauth_client_factory = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(provider)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=os.environ.get("TEST_RAISE_APP_EXCEPTIONS")=="1"),
        base_url="http://localhost:8080",
    ) as client:
        yield client, database, settings
    if app.state.core_provider.client:
        await app.state.core_provider.client.close()
    await engine.dispose()


def session_cookie(db) -> tuple[str, str]:
    user = str(uuid.uuid4())
    token = uuid.uuid4().hex + uuid.uuid4().hex
    db.execute(
        "INSERT INTO auth.application_users(id,display_name) VALUES(%s,'Test user')",
        (user,),
    )
    db.execute(
        "INSERT INTO auth.user_sessions(token_hash,user_id,expires_at) VALUES(%s,%s,%s)",
        (
            hashlib.sha256(token.encode()).hexdigest(),
            user,
            datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    db.commit()
    return user, token


@pytest.mark.asyncio
async def test_health_openapi_and_unauthenticated_profile(api_client):
    client, _, _ = api_client
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/api/v1/openapi.json")).status_code == 200
    response = await client.get("/api/v1/me")
    assert response.status_code == 401
    assert set(response.json()) == {"error", "code", "request_id"}


@pytest.mark.asyncio
async def test_csrf_and_logout_revoke_copied_cookie(api_client):
    client, db, _ = api_client
    _, token = session_cookie(db)
    client.cookies.set("horizon_session", token)
    assert (await client.get("/api/v1/me")).status_code == 200
    assert (
        await client.post(
            "/api/v1/auth/logout", headers={"Origin": "https://evil.test"}
        )
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/auth/logout", headers={"Origin": "http://localhost:8080"}
        )
    ).status_code == 204
    client.cookies.set("horizon_session", token)
    assert (await client.get("/api/v1/me")).status_code == 401


@pytest.mark.asyncio
async def test_key_secret_is_one_time_and_revocation_is_immediate(api_client):
    client, db, _ = api_client
    _, token = session_cookie(db)
    client.cookies.set("horizon_session", token)
    response = await client.post(
        "/api/v1/api-keys",
        json={"name": "Integration", "scopes": ["profile.read"]},
        headers={"Origin": "http://localhost:8080"},
    )
    assert response.status_code == 201, response.text
    key = response.json()
    assert "secret" in key
    listed = await client.get("/api/v1/api-keys")
    assert "secret" not in listed.json()[0]
    client.cookies.clear()
    auth = {"Authorization": "Bearer " + key["secret"]}
    assert (await client.get("/api/v1/me", headers=auth)).status_code == 200
    client.cookies.set("horizon_session", token)
    assert (
        await client.delete(
            "/api/v1/api-keys/" + key["id"], headers={"Origin": "http://localhost:8080"}
        )
    ).status_code == 204
    client.cookies.clear()
    assert (await client.get("/api/v1/me", headers=auth)).status_code == 401


@pytest.mark.asyncio
async def test_api_key_scope_and_resource_denials_precede_financial_call(api_client):
    client, db, settings = api_client
    user, _ = session_cookie(db)
    prefix = uuid.uuid4().hex[:12]
    secret = uuid.uuid4().hex * 2
    db.execute(
        "INSERT INTO auth.api_keys(id,owner_id,prefix,secret_hash,name,scopes,account_ids) VALUES(%s,%s,%s,%s,'Test',%s,%s)",
        (
            uuid.uuid4(),
            user,
            prefix,
            secret_hash(secret, settings),
            ["profile.read", "balance.read"],
            [],
        ),
    )
    db.commit()
    auth = {"Authorization": f"Bearer hbk_{prefix}_{secret}"}
    assert (
        await client.get(
            "/api/v1/accounts/" + str(uuid.uuid4()) + "/balance", headers=auth
        )
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/transfers",
            json={
                "source_account_id": str(uuid.uuid4()),
                "destination_account_id": str(uuid.uuid4()),
                "amount": "1.00",
            },
            headers=auth,
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_organization_role_does_not_grant_bank_admin(api_client):
    client, db, _ = api_client
    user, token = session_cookie(db)
    organization = uuid.uuid4()
    db.execute(
        "INSERT INTO auth.organizations(id,name,owner_id) VALUES(%s,%s,%s)",
        (organization, str(organization), user),
    )
    db.execute(
        "INSERT INTO auth.organization_members(id,organization_id,user_id,role) VALUES(%s,%s,%s,'administrator')",
        (uuid.uuid4(), organization, user),
    )
    db.commit()
    client.cookies.set("horizon_session", token)
    response = await client.post(
        "/api/v1/admin/issuances",
        json={
            "target_account_id": str(uuid.uuid4()),
            "amount": "1.00",
            "reason": "test",
            "authority_reference": "test",
        },
        headers={
            "Origin": "http://localhost:8080",
            "Idempotency-Key": uuid.uuid4().hex,
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_chunked_body_limit_cannot_be_bypassed(api_client):
    client, _, _ = api_client

    async def chunks():
        for _ in range(17):
            yield b"x" * 65536

    result = await client.post("/api/v1/transfers", content=chunks())
    assert result.status_code == 413


@pytest.mark.asyncio
async def test_blocked_owner_cannot_use_existing_api_key(api_client):
    client, db, settings = api_client
    user, _ = session_cookie(db)
    prefix = uuid.uuid4().hex[:12]
    secret = uuid.uuid4().hex * 2
    db.execute(
        "INSERT INTO auth.api_keys(id,owner_id,prefix,secret_hash,name,scopes) VALUES(%s,%s,%s,%s,'Test',%s)",
        (uuid.uuid4(), user, prefix, secret_hash(secret, settings), ["profile.read"]),
    )
    db.execute(
        "UPDATE auth.application_users SET status='blocked' WHERE id=%s", (user,)
    )
    db.commit()
    response = await client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer hbk_{prefix}_{secret}"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_oauth_state_is_single_use_and_relogin_keeps_one_profile(api_client,core):
    from urllib.parse import parse_qs, urlparse

    client, db, settings = api_client
    settings.grpc_target=core.test_target
    settings.internal_grpc_token=SecretStr(TOKEN)
    login = await client.get("/api/v1/auth/discord/login?age_confirmed=true")
    assert login.status_code == 302
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    bad = await client.get(
        "/api/v1/auth/discord/callback", params={"code": "test", "state": "incorrect"}
    )
    assert bad.status_code == 400
    callback = await client.get(
        "/api/v1/auth/discord/callback", params={"code": "test", "state": state}
    )
    assert callback.status_code == 302
    user = (await client.get("/api/v1/me")).json()["id"]
    client.cookies.set("horizon_oauth_state", state, domain="localhost.local", path="/")
    assert (
        await client.get(
            "/api/v1/auth/discord/callback", params={"code": "test", "state": state}
        )
    ).status_code == 400
    login2 = await client.get("/api/v1/auth/discord/login?age_confirmed=true")
    state2 = parse_qs(urlparse(login2.headers["location"]).query)["state"][0]
    old_session = client.cookies.get("horizon_session")
    assert (
        await client.get(
            "/api/v1/auth/discord/callback", params={"code": "test2", "state": state2}
        )
    ).status_code == 302
    assert (await client.get("/api/v1/me")).json()["id"] == user
    assert (
        db.execute(
            "SELECT COUNT(*) FROM auth.external_identities WHERE user_id=%s", (user,)
        ).fetchone()[0]
        == 1
    )
    assert db.execute(
        "SELECT revoked_at IS NOT NULL FROM auth.user_sessions WHERE token_hash=%s",
        (hashlib.sha256(old_session.encode()).hexdigest(),),
    ).fetchone()[0]


@pytest.mark.asyncio
async def test_investigations_and_audit_require_explicit_permissions(api_client):
    client, db, _ = api_client
    user, token = session_cookie(db)
    for permission in ("admin.investigations", "admin.audit"):
        db.execute(
            "INSERT INTO auth.user_permissions(user_id,permission,granted_by,reason) VALUES(%s,%s,%s,'test grant')",
            (user, permission, user),
        )
    db.commit()
    client.cookies.set("horizon_session", token)
    headers = {"Origin": "http://localhost:8080"}
    created = await client.post(
        "/api/v1/admin/investigations",
        headers=headers,
        json={
            "subject_type": "operation",
            "subject_id": str(uuid.uuid4()),
            "note": "Initial evidence",
        },
    )
    assert created.status_code == 201, created.text
    ident = created.json()["id"]
    assert (
        await client.post(
            f"/api/v1/admin/investigations/{ident}/notes",
            headers=headers,
            json={"body": "Follow-up evidence"},
        )
    ).status_code == 201
    assert (
        len((await client.get(f"/api/v1/admin/investigations/{ident}/notes")).json())
        == 2
    )
    assert (
        await client.post(
            f"/api/v1/admin/investigations/{ident}/close", headers=headers
        )
    ).status_code == 200
    assert (
        await client.post(
            f"/api/v1/admin/investigations/{ident}/notes",
            headers=headers,
            json={"body": "not permitted after close"},
        )
    ).status_code == 409
    audit = await client.get("/api/v1/admin/audit")
    assert audit.status_code == 200, audit.text
    assert any(
        event["action"] == "investigation.close" and event["target_id"] == ident
        for event in audit.json()["items"]
    )

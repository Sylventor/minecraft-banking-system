"""Real PostgreSQL sessions and core accounts; only Discord's external transport is simulated."""

import asyncio
import hashlib
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from test_api_security import api_client as api_client_fixture
from test_banking_experience import connect
from test_core_finance import core as core_fixture

api_client = api_client_fixture
core = core_fixture
ORIGIN = {"Origin": "http://localhost:8080"}
PASSWORD = "test long password phrase 你好 with spaces"


@pytest_asyncio.fixture
async def auth_client(api_client, core):
    client, db, settings = api_client
    connect(settings, core)
    # An independent client address per test, without weakening the actual database limiter.
    client._transport.client = (str(uuid.uuid4()), 123)
    return client, db, settings


async def register(client, name=None, password=PASSWORD):
    name = name or "player_" + uuid.uuid4().hex[:12]
    response = await client.post(
        "/api/v1/auth/register",
        headers=ORIGIN,
        json={
            "login": name,
            "password": password,
            "password_confirmation": password,
            "age_confirmed": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"], name


async def oauth(client):
    start = await client.get("/api/v1/auth/discord/login?age_confirmed=true")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    response = await client.get(
        "/api/v1/auth/discord/callback", params={"code": "test-code", "state": state}
    )
    assert response.status_code == 302, response.text
    return (await client.get("/api/v1/me")).json()["id"]


async def link(client):
    start = await client.post("/api/v1/auth/discord/link", headers=ORIGIN)
    assert start.status_code == 200, start.text
    state = parse_qs(urlparse(start.json()["url"]).query)["state"][0]
    response = await client.get(
        "/api/v1/auth/discord/callback", params={"code": "test-code", "state": state}
    )
    assert response.status_code == 302, response.text
    return response, state


@pytest.mark.asyncio
async def test_registration_argon2_salts_and_exactly_one_profile_and_primary_account(
    auth_client,
):
    client, db, _ = auth_client
    user, name = await register(client)
    profile = (await client.get("/api/v1/me")).json()
    assert (
        profile["id"] == user
        and profile["discord"] is None
        and profile["banking_ready"]
    )
    row = db.execute(
        "SELECT login,password_hash FROM auth.password_credentials WHERE user_id=%s",
        (user,),
    ).fetchone()
    assert row[0] == name.lower() and row[1].startswith(
        "$argon2id$v=19$m=65536,t=3,p=4$"
    )
    assert PasswordHasher().verify(row[1], PASSWORD)
    second, _ = await register(client)
    another = db.execute(
        "SELECT password_hash FROM auth.password_credentials WHERE user_id=%s",
        (second,),
    ).fetchone()[0]
    assert another != row[1] and PASSWORD not in str(row)
    assert (
        db.execute(
            "SELECT count(*) FROM finance.accounts WHERE owner_principal_id=%s AND account_type='personal'",
            (user,),
        ).fetchone()[0]
        == 1
    )
    assert (
        db.execute(
            "SELECT count(*) FROM auth.external_identities WHERE user_id=%s", (user,)
        ).fetchone()[0]
        == 0
    )
    db.commit()


@pytest.mark.asyncio
async def test_login_case_insensitive_neutral_failures_logout_and_persistence(
    auth_client,
):
    from app.config import get_settings
    from app.database import get_session
    from app.main import create_app

    client, db, settings = auth_client
    user, name = await register(client)
    await client.post("/api/v1/auth/logout", headers=ORIGIN)
    bad = await client.post(
        "/api/v1/auth/login",
        headers=ORIGIN,
        json={"login": name, "password": "test-wrong-password"},
    )
    absent = await client.post(
        "/api/v1/auth/login",
        headers=ORIGIN,
        json={
            "login": "missing_" + uuid.uuid4().hex[:8],
            "password": "test-wrong-password",
        },
    )
    assert bad.status_code == absent.status_code == 401
    assert bad.json()["error"] == absent.json()["error"] == "Неверный логин или пароль"
    valid = await client.post(
        "/api/v1/auth/login",
        headers=ORIGIN,
        json={"login": name.upper(), "password": PASSWORD},
    )
    assert valid.status_code == 200 and valid.json()["id"] == user
    assert (
        "HttpOnly" in valid.headers["set-cookie"]
        and "SameSite=lax" in valid.headers["set-cookie"]
    )
    assert valid.headers["cache-control"] == "no-store"
    token = client.cookies.get("horizon_session")
    stored = db.execute(
        "SELECT token_hash,auth_method FROM auth.user_sessions WHERE token_hash=%s",
        (hashlib.sha256(token.encode()).hexdigest(),),
    ).fetchone()
    assert stored[0] != token and stored[1] == "password"
    db.commit()
    fresh = create_app(settings)
    fresh.dependency_overrides[get_session] = (
        client._transport.app.dependency_overrides[get_session]
    )
    fresh.dependency_overrides[get_settings] = lambda: settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fresh),
        base_url="http://localhost:8080",
        cookies={"horizon_session": token},
    ) as restarted:
        assert (await restarted.get("/api/v1/me")).json()["id"] == user
    assert (
        await client.post("/api/v1/auth/session/refresh", headers=ORIGIN)
    ).status_code == 204
    methods = (await client.get("/api/v1/auth/methods")).json()
    assert (
        methods["auth_method"] == "password"
    )  # Refresh cannot turn password proof into Discord proof.
    old = client.cookies.get("horizon_session")
    assert (await client.post("/api/v1/auth/logout", headers=ORIGIN)).status_code == 204
    client.cookies.set("horizon_session", old)
    assert (await client.get("/api/v1/me")).status_code == 401


@pytest.mark.asyncio
async def test_unique_login_validation_and_no_automatic_merging(auth_client):
    client, db, _ = auth_client
    user, name = await register(client)
    before = db.execute("SELECT count(*) FROM auth.application_users").fetchone()[0]
    duplicate = await client.post(
        "/api/v1/auth/register",
        headers=ORIGIN,
        json={
            "login": name.upper(),
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
            "age_confirmed": True,
        },
    )
    assert duplicate.status_code == 409
    assert not (
        await client.get(
            "/api/v1/auth/login-availability", params={"login": name.upper()}
        )
    ).json()["available"]
    for invalid in ("admin", "bad login", "12abc"):
        result = await client.post(
            "/api/v1/auth/register",
            headers=ORIGIN,
            json={
                "login": invalid,
                "password": PASSWORD,
                "password_confirmation": PASSWORD,
                "age_confirmed": True,
            },
        )
        assert result.status_code == 422, result.text
    assert (
        db.execute("SELECT count(*) FROM auth.application_users").fetchone()[0]
        == before
    )
    assert (await client.get("/api/v1/me")).json()["id"] == user
    db.commit()


@pytest.mark.asyncio
async def test_password_validation_and_unauthenticated_login_csrf(auth_client):
    client, _, _ = auth_client
    for password, confirmation in (
        ("short", "short"),
        (PASSWORD, PASSWORD + "different"),
        ("x" * 1025, "x" * 1025),
    ):
        result = await client.post(
            "/api/v1/auth/register",
            headers=ORIGIN,
            json={
                "login": "player_" + uuid.uuid4().hex[:10],
                "password": password,
                "password_confirmation": confirmation,
            },
        )
        assert result.status_code == 422 and password not in result.text
    for endpoint in ("login", "register"):
        result = await client.post(
            "/api/v1/auth/" + endpoint,
            headers={"Origin": "https://evil.test"},
            json={
                "login": "player_test",
                "password": PASSWORD,
                "password_confirmation": PASSWORD,
                "age_confirmed": True,
            },
        )
        assert result.status_code == 403 and result.json()["code"] == "csrf_rejected"


@pytest.mark.asyncio
async def test_rate_limits_survive_app_boundaries_and_expire_without_locking_account(
    auth_client,
):
    client, db, _ = auth_client
    user, name = await register(client)
    for _ in range(10):
        result = await client.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"login": name, "password": "test-wrong-password"},
        )
        assert result.status_code == 401
    limited = await client.post(
        "/api/v1/auth/login", headers=ORIGIN, json={"login": name, "password": PASSWORD}
    )
    assert limited.status_code == 429 and limited.headers["retry-after"] == "60"
    db.execute(
        "UPDATE auth.authentication_limits SET window_start=now()-interval '2 minutes' WHERE bucket_hash=%s",
        (hashlib.sha256(("login-name:" + name).encode()).hexdigest(),),
    )
    db.commit()
    assert (
        await client.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"login": name, "password": PASSWORD},
        )
    ).status_code == 200
    assert (await client.get("/api/v1/me")).json()["id"] == user
    for _ in range(4):
        await register(client)
    blocked = await client.post(
        "/api/v1/auth/register",
        headers=ORIGIN,
        json={
            "login": "player_" + uuid.uuid4().hex[:10],
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
            "age_confirmed": True,
        },
    )
    assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_link_repeat_oauth_login_same_uuid_and_preserved_accounts(auth_client):
    client, db, _ = auth_client
    user, _ = await register(client)
    before = db.execute(
        "SELECT id,owner_principal_id,balance,held FROM finance.accounts WHERE owner_principal_id=%s ORDER BY id",
        (user,),
    ).fetchall()
    response, state = await link(client)
    assert "discord-linked" in response.headers["location"]
    replay = await client.get(
        "/api/v1/auth/discord/callback", params={"code": "test-code", "state": state}
    )
    assert replay.status_code == 400
    response, _ = await link(client)
    assert "discord-linked" in response.headers["location"]
    assert (
        db.execute(
            "SELECT count(*) FROM auth.external_identities WHERE user_id=%s AND provider='discord'",
            (user,),
        ).fetchone()[0]
        == 1
    )
    db.commit()
    await client.post("/api/v1/auth/logout", headers=ORIGIN)
    assert await oauth(client) == user
    assert (
        db.execute(
            "SELECT id,owner_principal_id,balance,held FROM finance.accounts WHERE owner_principal_id=%s ORDER BY id",
            (user,),
        ).fetchall()
        == before
    )
    db.commit()


@pytest.mark.asyncio
async def test_discord_conflict_does_not_merge_profiles_or_move_assets(auth_client):
    client, db, _ = auth_client
    discord_user = await oauth(client)
    await client.post("/api/v1/auth/logout", headers=ORIGIN)
    user, _ = await register(client)
    response, _ = await link(client)
    assert "discord-conflict" in response.headers["location"] and user != discord_user
    assert (await client.get("/api/v1/me")).json()["id"] == user
    assert (
        db.execute(
            "SELECT count(*) FROM auth.external_identities WHERE user_id=%s", (user,)
        ).fetchone()[0]
        == 0
    )
    assert (
        db.execute(
            "SELECT count(*) FROM finance.accounts WHERE owner_principal_id IN (%s,%s)",
            (user, discord_user),
        ).fetchone()[0]
        == 2
    )
    db.commit()


@pytest.mark.asyncio
async def test_link_bound_to_original_active_session_and_csrf(auth_client):
    client, _, _ = auth_client
    await register(client)
    rejected = await client.post(
        "/api/v1/auth/discord/link", headers={"Origin": "https://evil.test"}
    )
    assert rejected.status_code == 403
    start = await client.post("/api/v1/auth/discord/link", headers=ORIGIN)
    state = parse_qs(urlparse(start.json()["url"]).query)["state"][0]
    await register(client)
    rejected = await client.get(
        "/api/v1/auth/discord/callback", params={"state": state, "code": "test-code"}
    )
    assert rejected.status_code == 403
    assert (await client.get("/api/v1/auth/methods")).json()["discord"] is None


@pytest.mark.asyncio
async def test_legacy_discord_setup_unlink_last_method_and_password_recovery(
    auth_client,
):
    client, db, _ = auth_client
    user = await oauth(client)
    before = db.execute(
        "SELECT row_to_json(a)::text FROM finance.accounts a WHERE owner_principal_id=%s ORDER BY id",
        (user,),
    ).fetchall()
    db.commit()
    denied = await client.post(
        "/api/v1/auth/discord/unlink", headers=ORIGIN, json={"password": PASSWORD}
    )
    assert denied.status_code == 409
    name = "legacy_" + uuid.uuid4().hex[:10]
    setup = await client.post(
        "/api/v1/auth/password/setup",
        headers=ORIGIN,
        json={
            "login": name,
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
        },
    )
    assert setup.status_code == 204, setup.text
    assert (await client.get("/api/v1/me")).json()["id"] == user
    # A password session cannot recover a password without supplying the current one.
    body = {"password": PASSWORD + " new", "password_confirmation": PASSWORD + " new"}
    assert (
        await client.post("/api/v1/auth/password/change", headers=ORIGIN, json=body)
    ).status_code == 403
    assert await oauth(client) == user
    assert (
        await client.post("/api/v1/auth/password/change", headers=ORIGIN, json=body)
    ).status_code == 204
    wrong = await client.post(
        "/api/v1/auth/discord/unlink", headers=ORIGIN, json={"password": PASSWORD}
    )
    assert wrong.status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/discord/unlink",
            headers=ORIGIN,
            json={"password": PASSWORD + " new"},
        )
    ).status_code == 204
    assert (await client.get("/api/v1/auth/methods")).json()["discord"] is None
    assert (
        db.execute(
            "SELECT row_to_json(a)::text FROM finance.accounts a WHERE owner_principal_id=%s ORDER BY id",
            (user,),
        ).fetchall()
        == before
    )
    assert (
        db.execute(
            "SELECT count(*) FROM auth.user_sessions WHERE user_id=%s AND revoked_at IS NULL",
            (user,),
        ).fetchone()[0]
        == 1
    )
    db.commit()


@pytest.mark.asyncio
async def test_stale_identity_proof_change_password_and_others_revoked(auth_client):
    client, db, _ = auth_client
    user, name = await register(client)
    first = client.cookies.get("horizon_session")
    db.execute(
        "UPDATE auth.user_sessions SET created_at=now()-interval '20 minutes' WHERE user_id=%s",
        (user,),
    )
    db.commit()
    assert (
        await client.post("/api/v1/auth/discord/link", headers=ORIGIN)
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/auth/reauth", headers=ORIGIN, json={"password": PASSWORD}
        )
    ).status_code == 204
    assert (await client.get("/api/v1/auth/methods")).json()["recent"]
    body = {
        "current_password": PASSWORD,
        "password": PASSWORD + " changed",
        "password_confirmation": PASSWORD + " changed",
    }
    assert (
        await client.post("/api/v1/auth/password/change", headers=ORIGIN, json=body)
    ).status_code == 204
    async with httpx.AsyncClient(
        transport=client._transport,
        base_url="http://localhost:8080",
        cookies={"horizon_session": first},
    ) as copied:
        assert (await copied.get("/api/v1/me")).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"login": name, "password": PASSWORD},
        )
    ).status_code == 401


@pytest.mark.asyncio
async def test_parallel_registration_cannot_duplicate_casefolded_login(auth_client):
    client, db, _ = auth_client
    name = "parallel_" + uuid.uuid4().hex[:10]
    results = await asyncio.gather(
        *(
            client.post(
                "/api/v1/auth/register",
                headers=ORIGIN,
                json={
                    "login": login,
                    "password": PASSWORD,
                    "password_confirmation": PASSWORD,
                    "age_confirmed": True,
                },
            )
            for login in (name, name.upper())
        )
    )
    assert sorted(r.status_code for r in results) == [201, 409]
    assert (
        db.execute(
            "SELECT count(*) FROM auth.password_credentials WHERE login=%s", (name,)
        ).fetchone()[0]
        == 1
    )
    db.commit()


@pytest.mark.asyncio
async def test_bound_discord_reauth_refreshes_only_the_same_profile(auth_client):
    client, db, _ = auth_client
    user = await oauth(client)
    first = client.cookies.get("horizon_session")
    db.execute(
        "UPDATE auth.user_sessions SET created_at=now()-interval '20 minutes' WHERE user_id=%s",
        (user,),
    )
    db.commit()
    start = await client.post("/api/v1/auth/discord/reauth", headers=ORIGIN)
    assert start.status_code == 200
    state = parse_qs(urlparse(start.json()["url"]).query)["state"][0]
    response = await client.get(
        "/api/v1/auth/discord/callback", params={"code": "test-code", "state": state}
    )
    assert (
        response.status_code == 302
        and "discord-verified" in response.headers["location"]
    )
    assert client.cookies.get("horizon_session") != first
    assert (await client.get("/api/v1/me")).json()["id"] == user
    assert (await client.get("/api/v1/auth/methods")).json()["recent"]


@pytest.mark.asyncio
async def test_discord_reauth_cannot_create_or_switch_to_a_different_profile(
    auth_client,
):
    client, db, _ = auth_client
    user, _ = await register(client)
    before = db.execute("SELECT count(*) FROM auth.application_users").fetchone()[0]
    db.commit()
    start = await client.post("/api/v1/auth/discord/reauth", headers=ORIGIN)
    state = parse_qs(urlparse(start.json()["url"]).query)["state"][0]
    response = await client.get(
        "/api/v1/auth/discord/callback", params={"code": "test-code", "state": state}
    )
    assert (
        response.status_code == 302
        and "discord-conflict" in response.headers["location"]
    )
    assert (await client.get("/api/v1/me")).json()["id"] == user
    assert (await client.get("/api/v1/auth/methods")).json()["discord"] is None
    assert (
        db.execute("SELECT count(*) FROM auth.application_users").fetchone()[0]
        == before
    )
    db.commit()


@pytest.mark.asyncio
async def test_core_outage_after_registration_resumes_same_credentials_and_uuid(
    auth_client,
):
    from app.dependencies import get_core
    from app.grpc.client import CoreError

    client, db, _ = auth_client

    class Unavailable:
        async def primary_account(self, *_args):
            raise CoreError("UNAVAILABLE", "test core outage")

    app = client._transport.app
    app.dependency_overrides[get_core] = lambda: Unavailable()
    name = "resume_" + uuid.uuid4().hex[:12]
    body = {
        "login": name,
        "password": PASSWORD,
        "password_confirmation": PASSWORD,
        "age_confirmed": True,
    }
    response = await client.post("/api/v1/auth/register", headers=ORIGIN, json=body)
    assert response.status_code == 503
    user = str(
        db.execute(
            "SELECT user_id FROM auth.password_credentials WHERE login=%s", (name,)
        ).fetchone()[0]
    )
    db.commit()
    assert client.cookies.get("horizon_session") is None
    app.dependency_overrides.pop(get_core)
    response = await client.post(
        "/api/v1/auth/login", headers=ORIGIN, json={"login": name, "password": PASSWORD}
    )
    assert response.status_code == 200 and response.json()["id"] == user
    assert (
        db.execute(
            "SELECT count(*) FROM finance.accounts WHERE owner_principal_id=%s", (user,)
        ).fetchone()[0]
        == 1
    )
    db.commit()


@pytest.mark.asyncio
async def test_authentication_schema_does_not_advertise_api_keys_or_return_passwords(
    auth_client,
):
    client, _, _ = auth_client
    schema = (await client.get("/api/openapi.json")).json()
    for path in ("register", "login", "login-availability"):
        operation = next(iter(schema["paths"]["/api/v1/auth/" + path].values()))
        assert not operation.get("security")
    assert schema["paths"]["/api/v1/auth/methods"]["get"]["security"] == [
        {"Session": []}
    ]
    await register(client)
    for path in ("/api/v1/me", "/api/v1/auth/methods"):
        data = (await client.get(path)).json()
        assert "password_hash" not in str(data) and PASSWORD not in str(data)


@pytest.mark.asyncio
async def test_login_rereads_blocked_user_after_crossing_core_boundary(auth_client):
    from app.dependencies import get_core

    client, db, _ = auth_client
    user, name = await register(client)
    app = client._transport.app
    real = await app.state.core_provider.get()

    class BlockDuringLogin:
        async def primary_account(self, *args):
            result = await real.primary_account(*args)
            db.execute(
                "UPDATE auth.application_users SET status='blocked' WHERE id=%s",
                (user,),
            )
            db.commit()
            return result

    app.dependency_overrides[get_core] = lambda: BlockDuringLogin()
    result = await client.post(
        "/api/v1/auth/login", headers=ORIGIN, json={"login": name, "password": PASSWORD}
    )
    assert (
        result.status_code == 401
        and result.json()["error"] == "Неверный логин или пароль"
    )
    assert (await client.get("/api/v1/me")).status_code == 401

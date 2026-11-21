"""2.2 against real PostgreSQL and native C++ gRPC. Only Discord transport is simulated."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_api_security import api_client as api_fixture
from test_api_security import session_cookie
from test_core_finance import (
    TOKEN,
    account,
    authority,
    context,
    fund,
    invoke,
    pb,
    reconciled,
    request,
)
from test_core_finance import core as core_fixture

api_client = api_fixture
core = core_fixture
ORIGIN = {"Origin": "http://localhost:8080"}


@pytest.mark.asyncio
async def test_core_events_are_visible_only_after_commit_and_rollback_discards_them(
    api_client, core
):
    client, db, _ = api_client
    actor, _ = login(client, db)
    a = account(core, actor)
    db.execute(
        "UPDATE finance.event_outbox SET acknowledged_at=now() WHERE principal_id<>%s",
        (actor,),
    )
    db.commit()
    relay_context = pb.RequestContext(
        actor_id=actor, request_id="commit-test", source="notification-relay"
    )

    def events():
        return json.loads(
            invoke(
                core.NotificationEvents,
                pb.NotificationEventsRequest(context=relay_context),
            ).json
        )["items"]

    initial = {x["id"] for x in events()}
    db.execute("UPDATE finance.accounts SET status='frozen' WHERE id=%s", (a,))
    assert {x["id"] for x in events()} == initial
    db.rollback()
    assert {x["id"] for x in events()} == initial
    db.execute("UPDATE finance.accounts SET status='frozen' WHERE id=%s", (a,))
    db.commit()
    assert any(
        x["payload"].get("type") == "account.status" and x["principal_id"] == actor
        for x in events()
    )


def login(client, db):
    actor, cookie = session_cookie(db)
    client.cookies.set("horizon_session", cookie)
    return actor, cookie


def role(db, user, key):
    ident = db.execute(
        "SELECT id FROM auth.admin_roles WHERE system_key=%s", (key,)
    ).fetchone()[0]
    db.execute(
        "INSERT INTO auth.user_roles(user_id,role_id,granted_by,reason) VALUES(%s,%s,%s,'test setup') ON CONFLICT DO NOTHING",
        (user, ident, user),
    )
    db.commit()
    return str(ident)


def legacy(db, user, *permissions):
    for p in permissions:
        db.execute(
            "INSERT INTO auth.user_permissions(user_id,permission,granted_by,reason) VALUES(%s,%s,%s,'legacy preserved') ON CONFLICT DO NOTHING",
            (user, p, user),
        )
    db.commit()


@pytest.mark.asyncio
async def test_role_assignment_revocation_union_and_no_financial_bypass(
    api_client, core
):
    client, db, settings = api_client
    settings.grpc_target = core.test_target
    settings.internal_grpc_token = SecretStr(TOKEN)
    actor, cookie = login(client, db)
    role(db, actor, "superadmin")
    target, target_cookie = session_cookie(db)
    legacy(db, target, "admin.audit")
    created = await client.post(
        "/api/v1/admin/roles",
        headers=ORIGIN,
        json={
            "name": "Custom " + uuid.uuid4().hex,
            "description": "Support",
            "permissions": ["admin.users.read"],
        },
    )
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    url = f"/api/v1/admin/users/{target}/roles/{rid}"
    assert (
        await client.put(
            url, headers=ORIGIN, json={"confirmed": True, "reason": "Support shift"}
        )
    ).status_code == 200
    rights = (await client.get(f"/api/v1/admin/users/{target}/permissions")).json()[
        "permissions"
    ]
    assert {"admin.audit", "admin.users.read"} <= set(rights)
    assert (
        await client.post(
            url + "/revoke",
            headers=ORIGIN,
            json={"confirmed": True, "reason": "Shift ended"},
        )
    ).status_code == 200
    rights = (await client.get(f"/api/v1/admin/users/{target}/permissions")).json()[
        "permissions"
    ]
    assert "admin.audit" in rights and "admin.users.read" not in rights
    assert (
        await client.put(
            f"/api/v1/admin/users/{actor}/roles/{rid}",
            headers=ORIGIN,
            json={"confirmed": True, "reason": "Self promotion"},
        )
    ).status_code == 403
    # Even a full backend Superadmin cannot read or mutate finance without a core grant.
    result = await client.get("/api/v1/admin/accounts")
    assert result.status_code == 403, result.text
    destination = account(core, actor)
    result = await client.post(
        "/api/v1/admin/issuances",
        headers={**ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "target_account_id": destination,
            "amount": "1.00",
            "reason": "Must fail",
            "authority_reference": "test",
        },
    )
    assert result.status_code == 403, result.text
    assert (
        db.execute(
            "SELECT count(*) FROM finance.authorities WHERE principal_id=%s", (actor,)
        ).fetchone()[0]
        == 0
    )


@pytest.mark.asyncio
async def test_cannot_delegate_unheld_or_modify_own_role_and_auditor_readonly(
    api_client,
):
    client, db, _ = api_client
    actor, _ = login(client, db)
    legacy(db, actor, "admin.roles.manage")
    response = await client.post(
        "/api/v1/admin/roles",
        headers=ORIGIN,
        json={
            "name": "Escalation " + uuid.uuid4().hex,
            "permissions": ["admin.issuance"],
        },
    )
    assert response.status_code == 403, response.text
    auditor = str(
        db.execute(
            "SELECT id FROM auth.admin_roles WHERE system_key='auditor'"
        ).fetchone()[0]
    )
    target, _ = session_cookie(db)
    assert (
        await client.put(
            f"/api/v1/admin/users/{target}/roles/{auditor}",
            headers=ORIGIN,
            json={"confirmed": True, "reason": "Escalation"},
        )
    ).status_code == 403
    role(db, actor, "auditor")
    assert (await client.get("/api/v1/admin/investigations")).status_code == 200
    assert (
        await client.post(
            "/api/v1/admin/investigations",
            headers=ORIGIN,
            json={"subject_type": "user", "subject_id": target, "note": "Denied"},
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_last_superadmin_and_system_templates_protected(api_client):
    client, db, _ = api_client
    actor, _ = login(client, db)
    legacy(db, actor, "admin.roles.manage", "admin.users")
    target, _ = session_cookie(db)
    super_role = role(db, target, "superadmin")
    # Isolate active template membership without touching users or direct grants.
    db.execute(
        "UPDATE auth.user_roles SET revoked_at=now() WHERE role_id=%s AND user_id<>%s",
        (super_role, target),
    )
    db.commit()
    # Grant the managing test actor the registry explicitly, not Superadmin membership.
    for (p,) in db.execute(
        "SELECT permission FROM auth.permission_registry"
    ).fetchall():
        legacy(db, actor, p)
    response = await client.post(
        f"/api/v1/admin/users/{target}/roles/{super_role}/revoke",
        headers=ORIGIN,
        json={"confirmed": True, "reason": "Must preserve last"},
    )
    assert response.status_code == 409, response.text
    response = await client.post(
        f"/api/v1/admin/users/{target}/state",
        headers=ORIGIN,
        json={"status": "blocked", "reason": "Must preserve last"},
    )
    assert response.status_code == 409, response.text
    response = await client.patch(
        f"/api/v1/admin/roles/{super_role}",
        headers=ORIGIN,
        json={"name": "Changed", "permissions": []},
    )
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_user_search_and_core_admin_queries_delegation_account_status(
    api_client, core
):
    client, db, settings = api_client
    settings.grpc_target = core.test_target
    settings.internal_grpc_token = SecretStr(TOKEN)
    actor, _ = login(client, db)
    role(db, actor, "superadmin")
    target, _ = session_cookie(db)
    nick = "Search" + uuid.uuid4().hex[:8]
    discord = str(uuid.uuid4().int >> 64)
    db.execute(
        "UPDATE auth.application_users SET bank_username=%s WHERE id=%s", (nick, target)
    )
    db.execute(
        "INSERT INTO auth.external_identities(id,user_id,provider,external_id,username) VALUES(%s,%s,'discord',%s,'User')",
        (uuid.uuid4(), target, discord),
    )
    db.commit()
    for term in (nick, discord):
        response = await client.get(
            "/api/v1/admin/users", params={"query": term, "limit": 1}
        )
        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["id"] == target
    authority(db, actor, "admin.read")
    a = account(core, target)
    accounts = await client.get("/api/v1/admin/accounts", params={"owner_id": target})
    assert accounts.status_code == 200, accounts.text
    assert a in {x["id"] for x in accounts.json()["items"]}
    assert (await client.get("/api/v1/admin/transactions")).status_code == 200
    assert (await client.get("/api/v1/admin/overview")).json()["core"] == "ready"
    payload = {
        "user_id": target,
        "permission": "admin.read",
        "confirmed": True,
        "reason": "Read-only mandate",
    }
    assert (
        await client.post(
            "/api/v1/admin/financial-permissions", headers=ORIGIN, json=payload
        )
    ).status_code == 403
    authority(db, actor, "authority.manage")
    assert (
        await client.post(
            "/api/v1/admin/financial-permissions", headers=ORIGIN, json=payload
        )
    ).status_code == 200
    assert (
        await client.post(
            "/api/v1/admin/financial-permissions",
            headers=ORIGIN,
            json={**payload, "user_id": actor},
        )
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/admin/financial-permissions",
            headers=ORIGIN,
            json={**payload, "permission": "issue.create"},
        )
    ).status_code == 403
    authority(db, actor, "account.state.manage")
    response = await client.post(
        f"/api/v1/admin/accounts/{a}/state",
        headers=ORIGIN,
        json={"state": "frozen", "confirmed": True, "reason": "Review"},
    )
    assert response.status_code == 200, response.text
    assert (
        db.execute("SELECT status FROM finance.accounts WHERE id=%s", (a,)).fetchone()[
            0
        ]
        == "frozen"
    )
    assert (
        await client.get(
            "/api/v1/admin/audit",
            params={
                "actor_id": actor,
                "action": "finance.",
                "from_date": "2020-01-01T00:00:00Z",
            },
        )
    ).status_code == 200
    reconciled(db)


@pytest.mark.asyncio
async def test_terms_empty_publish_history_restore_unpublish_and_permissions(
    api_client,
):
    client, db, _ = api_client
    db.execute(
        "UPDATE auth.terms_versions SET status='unpublished' WHERE status='published'"
    )
    db.commit()
    assert (await client.get("/api/v1/terms")).json() == {"current": None}
    actor, _ = login(client, db)
    upload = {
        "filename": "rules.md",
        "markdown": "# Правила\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n[Банк](https://example.com)",
        "confirmed": True,
    }
    assert (
        await client.post("/api/v1/admin/terms", headers=ORIGIN, json=upload)
    ).status_code == 403
    legacy(db, actor, "admin.terms.manage")
    first = await client.post("/api/v1/admin/terms", headers=ORIGIN, json=upload)
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]
    second = await client.post(
        "/api/v1/admin/terms",
        headers=ORIGIN,
        json={**upload, "markdown": "# Вторая версия"},
    )
    assert second.status_code == 201, second.text
    restored = await client.post(
        f"/api/v1/admin/terms/{first_id}/restore",
        headers=ORIGIN,
        json={"confirmed": True},
    )
    assert restored.status_code == 201, restored.text
    assert restored.json()["version"] > second.json()["version"]
    assert restored.json()["restored_from"] == first_id
    assert len((await client.get("/api/v1/admin/terms")).json()["items"]) >= 3
    assert (
        db.execute(
            "SELECT count(*) FROM auth.terms_versions WHERE status='published'"
        ).fetchone()[0]
        == 1
    )
    client.cookies.clear()
    assert (await client.get("/api/v1/terms")).json()["current"]["markdown"] == upload[
        "markdown"
    ]
    actor, cookie = login(client, db)
    legacy(db, actor, "admin.terms.manage")
    assert (
        await client.post(
            "/api/v1/admin/terms/unpublish", headers=ORIGIN, json={"confirmed": True}
        )
    ).status_code == 200
    assert (await client.get("/api/v1/terms")).json()["current"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename,markdown",
    [
        ("rules.exe", "# Bad"),
        ("rules.md", "<script>alert(1)</script>"),
        ("rules.md", "[x](javascript:alert(1))"),
        ("rules.md", "[x](java%73cript:alert(1))"),
        ("rules.md", "bad\x00data"),
        ("rules.md", "я" * 70000),
    ],
    ids=["extension", "html", "javascript", "encoded-scheme", "binary", "oversized"],
)
async def test_terms_reject_unsafe_content(api_client, filename, markdown):
    client, db, _ = api_client
    actor, _ = login(client, db)
    legacy(db, actor, "admin.terms.manage")
    response = await client.post(
        "/api/v1/admin/terms",
        headers=ORIGIN,
        json={"filename": filename, "markdown": markdown, "confirmed": True},
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_concurrent_publications_have_one_active_version(api_client):
    client, db, _ = api_client
    actor, _ = login(client, db)
    legacy(db, actor, "admin.terms.manage")
    results = await asyncio.gather(
        *(
            client.post(
                "/api/v1/admin/terms",
                headers=ORIGIN,
                json={
                    "filename": "rules.md",
                    "markdown": "# Version " + str(i),
                    "confirmed": True,
                },
            )
            for i in range(2)
        )
    )
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    assert (
        db.execute(
            "SELECT count(*) FROM auth.terms_versions WHERE status='published'"
        ).fetchone()[0]
        == 1
    )


async def drain(client, settings):
    from app.notifications import relay

    engine = create_async_engine(settings.auth_database_url)
    try:
        async with async_sessionmaker(engine)() as session:
            for _ in range(100):
                if (
                    await relay(
                        session, await client._transport.app.state.core_provider.get()
                    )
                    < 100
                ):
                    break
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_organization_events_respect_membership_and_internal_transfer_categories(
    api_client, core
):
    client, db, settings = api_client
    settings.grpc_target = core.test_target
    settings.internal_grpc_token = SecretStr(TOKEN)
    actor, _ = login(client, db)
    viewer, _ = session_cookie(db)
    limited, _ = session_cookie(db)
    db.execute(
        "INSERT INTO auth.external_identities(id,user_id,provider,external_id,username) VALUES(%s,%s,'discord',%s,'Org test')",
        (uuid.uuid4(), actor, str(uuid.uuid4().int >> 64)),
    )
    db.commit()
    source = account(core, actor)
    fund(core, db, actor, source, "10.00")
    response = await client.post(
        "/api/v1/organizations",
        headers=ORIGIN,
        json={"name": "Outbox " + uuid.uuid4().hex[:12]},
    )
    assert response.status_code == 201, response.text
    org = response.json()["id"]
    response = await client.post(
        f"/api/v1/organizations/{org}/accounts",
        headers={**ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        json={"name": "Организация"},
    )
    assert response.status_code == 201, response.text
    destination = response.json()["id"]
    for user, perms in (
        (viewer, ["accounts.read", "balance.read", "history.read"]),
        (limited, ["accounts.read", "balance.read"]),
    ):
        response = await client.put(
            f"/api/v1/organizations/{org}/members/{user}",
            headers=ORIGIN,
            json={"user_id": user, "role": "member", "permissions": perms},
        )
        assert response.status_code == 200, response.text
    assert (
        await client.put(
            "/api/v1/me/notification-settings",
            headers=ORIGIN,
            json={
                "outgoing_enabled": False,
                "incoming_enabled": False,
                "organizations_enabled": True,
            },
        )
    ).status_code == 200
    op = invoke(core.Transfer, request(actor, source, destination, "1.00"))
    await drain(client, settings)
    events = db.execute(
        "SELECT id,user_id,payload FROM auth.discord_outbox WHERE payload->>'operation_id'=%s",
        (op.id,),
    ).fetchall()
    assert {str(e[1]) for e in events} == {actor, viewer}
    own = next(e for e in events if str(e[1]) == actor)
    assert own[2]["direction"] == "internal" and set(own[2]["categories"]) == {
        "outgoing",
        "organizations",
    }
    db.execute(
        "UPDATE auth.discord_outbox SET status='suppressed' WHERE id<>%s", (own[0],)
    )
    db.commit()
    bot_headers = {
        **ORIGIN,
        "X-Horizon-Bot-Token": settings.discord_bot_internal_token.get_secret_value(),
    }
    claimed = (
        await client.post(
            "/api/v1/internal/notifications/claim", headers=bot_headers, json={}
        )
    ).json()["items"]
    assert len(claimed) == 1 and claimed[0]["id"] == str(own[0])
    assert (
        await client.put(
            "/api/v1/me/notification-settings",
            headers=ORIGIN,
            json={
                "outgoing_enabled": False,
                "incoming_enabled": False,
                "organizations_enabled": False,
            },
        )
    ).status_code == 200
    second = invoke(core.Transfer, request(actor, source, destination, "1.00"))
    await drain(client, settings)
    ident = db.execute(
        "SELECT id FROM auth.discord_outbox WHERE user_id=%s AND payload->>'operation_id'=%s",
        (actor, second.id),
    ).fetchone()[0]
    db.execute(
        "UPDATE auth.discord_outbox SET status='suppressed' WHERE id<>%s", (ident,)
    )
    db.commit()
    assert (
        await client.post(
            "/api/v1/internal/notifications/claim", headers=bot_headers, json={}
        )
    ).json()["items"] == []
    assert (
        db.execute(
            "SELECT status FROM auth.discord_outbox WHERE id=%s", (ident,)
        ).fetchone()[0]
        == "suppressed"
    )


@pytest.mark.asyncio
async def test_delivery_recovers_expired_lease_and_stops_after_six_attempts(api_client):
    client, db, settings = api_client
    actor, _ = login(client, db)
    db.execute(
        "INSERT INTO auth.external_identities(id,user_id,provider,external_id,username) VALUES(%s,%s,'discord',%s,'Lease test')",
        (uuid.uuid4(), actor, str(uuid.uuid4().int >> 64)),
    )
    db.execute("UPDATE auth.discord_outbox SET status='suppressed'")
    db.commit()
    assert (
        await client.patch(
            "/api/v1/me/settings",
            headers=ORIGIN,
            json={"bank_username": "Lease" + uuid.uuid4().hex[:12]},
        )
    ).status_code == 200
    headers = {
        **ORIGIN,
        "X-Horizon-Bot-Token": settings.discord_bot_internal_token.get_secret_value(),
    }

    async def claim():
        response = await client.post(
            "/api/v1/internal/notifications/claim", headers=headers, json={}
        )
        assert response.status_code == 200, response.text
        return response.json()["items"][0]

    first = await claim()
    db.execute(
        "UPDATE auth.discord_outbox SET lease_until=now()-interval '1 second' WHERE id=%s",
        (first["id"],),
    )
    db.commit()
    recovered = await claim()
    assert recovered["id"] == first["id"] and recovered["nonce"] == first["nonce"]
    assert recovered["lease_token"] != first["lease_token"]
    item = recovered
    for attempt in range(2, 7):
        response = await client.post(
            f"/api/v1/internal/notifications/{item['id']}/result",
            headers=headers,
            json={
                "lease_token": item["lease_token"],
                "outcome": "retry",
                "error": "discord_unavailable",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == ("failed" if attempt == 6 else "pending")
        db.execute(
            "UPDATE auth.discord_outbox SET available_at=now()-interval '1 second' WHERE id=%s",
            (item["id"],),
        )
        db.commit()
        if attempt < 6:
            item = await claim()
    assert (
        await client.post(
            "/api/v1/internal/notifications/claim", headers=headers, json={}
        )
    ).json()["items"] == []


@pytest.mark.asyncio
async def test_confirmed_finance_events_delivery_retry_dedup_and_preferences(
    api_client, core
):
    client, db, settings = api_client
    settings.grpc_target = core.test_target
    settings.internal_grpc_token = SecretStr(TOKEN)
    sender, _ = login(client, db)
    recipient, _ = session_cookie(db)
    for user in (sender, recipient):
        db.execute(
            "INSERT INTO auth.external_identities(id,user_id,provider,external_id,username) VALUES(%s,%s,'discord',%s,'DM test')",
            (uuid.uuid4(), user, str(uuid.uuid4().int >> 64)),
        )
    db.commit()
    source, destination = account(core, sender), account(core, recipient)
    fund(core, db, sender, source, "20.00")
    command = request(sender, source, destination, "5.00")
    operation = invoke(core.Transfer, command)
    assert invoke(core.Transfer, command).id == operation.id
    await drain(client, settings)
    await drain(client, settings)
    deliveries = db.execute(
        "SELECT user_id,payload FROM auth.discord_outbox WHERE payload->>'operation_id'=%s ORDER BY user_id",
        (operation.id,),
    ).fetchall()
    assert {str(x[0]) for x in deliveries} == {sender, recipient} and len(
        deliveries
    ) == 2
    assert {x[1]["direction"] for x in deliveries} == {"incoming", "outgoing"}
    # Outbox write is in the core transaction and rolls back with failed evidence/cache checks.
    before = db.execute("SELECT count(*) FROM finance.event_outbox").fetchone()[0]
    bad = request(sender, source, destination, "100.00")
    with pytest.raises(Exception):
        invoke(core.Transfer, bad)
    assert (
        db.execute("SELECT count(*) FROM finance.event_outbox").fetchone()[0] == before
    )
    db.execute(
        "UPDATE auth.discord_outbox SET status='suppressed' WHERE payload->>'operation_id' IS DISTINCT FROM %s",
        (operation.id,),
    )
    db.commit()
    bot_headers = {
        **ORIGIN,
        "X-Horizon-Bot-Token": settings.discord_bot_internal_token.get_secret_value(),
    }
    assert (
        await client.post(
            "/api/v1/internal/notifications/claim", headers=ORIGIN, json={}
        )
    ).status_code == 401
    first = (
        await client.post(
            "/api/v1/internal/notifications/claim", headers=bot_headers, json={}
        )
    ).json()["items"][0]
    assert (
        await client.post(
            "/api/v1/internal/notifications/claim", headers=bot_headers, json={}
        )
    ).json()["items"][0]["id"] != first["id"]
    retry = await client.post(
        f"/api/v1/internal/notifications/{first['id']}/result",
        headers=bot_headers,
        json={
            "lease_token": first["lease_token"],
            "outcome": "retry",
            "error": "discord_unavailable",
        },
    )
    assert retry.json()["status"] == "pending", retry.text
    db.execute(
        "UPDATE auth.discord_outbox SET available_at=now()-interval '1 second' WHERE id=%s",
        (first["id"],),
    )
    db.commit()
    repeated = (
        await client.post(
            "/api/v1/internal/notifications/claim", headers=bot_headers, json={}
        )
    ).json()["items"][0]
    assert (
        repeated["id"] == first["id"]
        and repeated["nonce"] == first["nonce"]
        and repeated["lease_token"] != first["lease_token"]
    )
    url = f"/api/v1/internal/notifications/{first['id']}/result"
    assert (
        await client.post(
            url,
            headers=bot_headers,
            json={"lease_token": first["lease_token"], "outcome": "sent"},
        )
    ).status_code == 409
    assert (
        await client.post(
            url,
            headers=bot_headers,
            json={
                "lease_token": repeated["lease_token"],
                "outcome": "sent",
                "message_id": "1234",
            },
        )
    ).json()["status"] == "sent"
    assert (await client.get("/api/v1/me/notification-deliveries")).status_code == 200
    reconciled(db)


@pytest.mark.asyncio
async def test_disabled_notifications_dm_failure_and_critical_override(
    api_client, core
):
    client, db, settings = api_client
    settings.grpc_target = core.test_target
    settings.internal_grpc_token = SecretStr(TOKEN)
    user, _ = login(client, db)
    db.execute(
        "INSERT INTO auth.external_identities(id,user_id,provider,external_id,username) VALUES(%s,%s,'discord',%s,'DM test')",
        (uuid.uuid4(), user, str(uuid.uuid4().int >> 64)),
    )
    db.commit()
    assert (
        await client.put(
            "/api/v1/me/notification-settings",
            headers=ORIGIN,
            json={"discord_enabled": False},
        )
    ).status_code == 200
    a = account(core, user)
    fund(core, db, user, a, "2.00")
    await drain(client, settings)
    db.execute(
        "UPDATE auth.discord_outbox SET status='suppressed' WHERE user_id<>%s", (user,)
    )
    db.commit()
    bot_headers = {
        **ORIGIN,
        "X-Horizon-Bot-Token": settings.discord_bot_internal_token.get_secret_value(),
    }
    found = []
    for _ in range(10):
        items = (
            await client.post(
                "/api/v1/internal/notifications/claim", headers=bot_headers, json={}
            )
        ).json()["items"]
        if not items:
            continue
        item = items[0]
        found.append(item)
        result = await client.post(
            f"/api/v1/internal/notifications/{item['id']}/result",
            headers=bot_headers,
            json={
                "lease_token": item["lease_token"],
                "outcome": "failed",
                "error": "dm_closed",
            },
        )
        assert result.json()["status"] == "failed"
    assert found and all(not i["payload"].get("operation_id") for i in found)
    assert (
        db.execute(
            "SELECT count(*) FROM auth.discord_outbox WHERE user_id=%s AND status='suppressed' AND category='incoming'",
            (user,),
        ).fetchone()[0]
        >= 1
    )
    assert (
        db.execute(
            "SELECT count(*) FROM auth.discord_outbox WHERE user_id=%s AND status='failed' AND last_error='dm_closed'",
            (user,),
        ).fetchone()[0]
        >= 1
    )

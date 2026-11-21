from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr
from test_api_security import api_client as api_fixture
from test_api_security import session_cookie
from test_core_finance import TOKEN, account, context, fund, invoke, pb, reconciled
from test_core_finance import core as core_fixture

api_client = api_fixture
core = core_fixture
ORIGIN = {"Origin": "http://localhost:8080"}


@pytest.mark.asyncio
async def test_public_documentation_excludes_admin_and_verifier(api_client):
    client, _, _ = api_client
    schema = await client.get("/api/openapi.json")
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert "/api/v1/transfer-quotes" in paths
    assert "/api/v1/recipients/search" in paths
    assert "/api/v1/minecraft/link/verify" in paths
    assert not any("/admin/" in path or path == "/api/v1/minecraft/verify" for path in paths)
    assert "IssueRequest" not in schema.json()["components"]["schemas"]
    docs = await client.get("/api/docs")
    assert docs.status_code == 200
    assert "Поиск" in docs.text and "Python" in docs.text


@pytest.mark.asyncio
async def test_bot_transport_uses_real_primary_metadata_search_and_quote(
    api_client, core
):
    from bot.app.api import HorizonApi

    client, db, settings = api_client
    connect(settings, core)
    sender, _, source = await banker(client, db, "BotSender")
    _, _, _ = await banker(client, db, "BotReceiver")
    discord_id = str(uuid.uuid4().int >> 64)
    db.execute(
        "INSERT INTO auth.external_identities(id,user_id,provider,external_id,username) VALUES(%s,%s,'discord',%s,'bot-sender')",
        (uuid.uuid4(), sender, discord_id),
    )
    db.commit()
    fund(core, db, sender, source["id"], "5.00")
    api = HorizonApi(
        "http://localhost:8080/api/v1",
        settings.discord_bot_internal_token.get_secret_value(),
    )
    await api.client.aclose()
    api.client = httpx.AsyncClient(
        transport=client._transport, base_url="http://localhost:8080/api/v1/"
    )
    try:
        primary = await api.get("/accounts/primary", int(discord_id))
        assert primary["id"] == source["id"]
        accounts = await api.get("/accounts?limit=25", int(discord_id))
        assert accounts["items"][0]["public_number"].startswith("HB-")
        assert "transfer" in accounts["items"][0]["permissions"]
        found = await api.get("/recipients/search?q=BotReceiver", int(discord_id))
        assert len(found["items"]) == 1
        quote = await api.post(
            "/transfer-quotes",
            int(discord_id),
            {
                "recipient": {"kind": "user", "value": found["items"][0]["id"]},
                "amount": "1.00",
                "reason": "я" * 500,
            },
        )
        receipt = await api.post(
            "/transfer-quotes/" + quote["id"] + "/confirm",
            int(discord_id),
            {},
            quote["id"],
        )
        repeated = await api.post(
            "/transfer-quotes/" + quote["id"] + "/confirm",
            int(discord_id),
            {},
            quote["id"],
        )
        assert receipt["id"] == repeated["id"]
        assert receipt["reason"] == "я" * 500
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_raw_key_collision_cannot_substitute_quote_receipt(api_client, core):
    from test_core_finance import request as money_request

    client, db, settings = api_client
    connect(settings, core)
    sender, token, source = await banker(client, db, "CollisionSender")
    _, _, target = await banker(client, db, "CollisionReceiver")
    fund(core, db, sender, source["id"], "10.00")
    client.cookies.set("horizon_session", token)
    quote = (
        await client.post(
            "/api/v1/transfer-quotes",
            json={
                "recipient": {"kind": "bank_name", "value": "CollisionReceiver"},
                "amount": "2.00",
            },
            headers=ORIGIN,
        )
    ).json()
    command = money_request(
        sender, source["id"], target["id"], "3.00", key="quote:" + quote["id"]
    )
    invoke(core.Transfer, command)
    response = await client.post(
        "/api/v1/transfer-quotes/" + quote["id"] + "/confirm",
        headers={**ORIGIN, "Idempotency-Key": quote["id"]},
    )
    assert response.status_code == 409
    assert (
        db.execute(
            "SELECT operation_id FROM auth.transfer_quotes WHERE id=%s", (quote["id"],)
        ).fetchone()[0]
        is None
    )
    assert (
        db.execute(
            "SELECT balance::text FROM finance.accounts WHERE id=%s", (target["id"],)
        ).fetchone()[0]
        == "3.00"
    )


def connect(settings, core):
    settings.grpc_target = core.test_target
    settings.internal_grpc_token = SecretStr(TOKEN)


async def banker(client, db, name):
    actor, token = session_cookie(db)
    client.cookies.set("horizon_session", token)
    profile = await client.patch(
        "/api/v1/me/settings", json={"bank_username": name}, headers=ORIGIN
    )
    assert profile.status_code == 200, profile.text
    dashboard = await client.get("/api/v1/dashboard")
    assert dashboard.status_code == 200, dashboard.text
    return actor, token, dashboard.json()["primary_account"]


def test_primary_account_is_unique_under_parallel_creation_and_adopts_existing(
    core, database
):
    actor = str(uuid.uuid4())
    existing = account(core, actor)

    def ensure(_):
        return invoke(
            core.EnsurePrimaryAccount, pb.PrimaryAccountRequest(context=context(actor))
        ).id

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(ensure, range(24)))
    assert set(results) == {existing}
    assert (
        database.execute(
            "SELECT count(*) FROM finance.accounts WHERE owner_principal_id=%s",
            (actor,),
        ).fetchone()[0]
        == 1
    )
    assert (
        database.execute(
            "SELECT count(*) FROM finance.primary_accounts WHERE principal_id=%s",
            (actor,),
        ).fetchone()[0]
        == 1
    )
    assert (
        database.execute(
            "SELECT public_number FROM finance.accounts WHERE id=%s", (existing,)
        )
        .fetchone()[0]
        .startswith("HB-")
    )


@pytest.mark.asyncio
async def test_parallel_oauth_registers_one_identity_and_one_primary_account(
    api_client, core
):
    client, db, settings = api_client
    connect(settings, core)
    app = client._transport.app

    async def login():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://localhost:8080",
        ) as browser:
            start = await browser.get("/api/v1/auth/discord/login?age_confirmed=true")
            state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
            response = await browser.get(
                "/api/v1/auth/discord/callback",
                params={"state": state, "code": "test-provider-code"},
            )
            assert response.status_code == 302, response.text
            profile = (await browser.get("/api/v1/me")).json()
            dashboard = await browser.get("/api/v1/dashboard")
            assert dashboard.status_code == 200, dashboard.text
            assert profile["banking_ready"]
            return profile["id"], dashboard.json()["primary_account"]["id"]

    results = await asyncio.gather(*(login() for _ in range(6)))
    assert len(set(results)) == 1
    user, primary = results[0]
    assert (
        db.execute(
            "SELECT count(*) FROM auth.external_identities WHERE user_id=%s", (user,)
        ).fetchone()[0]
        == 1
    )
    assert (
        db.execute(
            "SELECT count(*) FROM finance.accounts WHERE owner_principal_id=%s", (user,)
        ).fetchone()[0]
        == 1
    )
    assert (
        db.execute(
            "SELECT account_id::text FROM finance.primary_accounts WHERE principal_id=%s",
            (user,),
        ).fetchone()[0]
        == primary
    )


@pytest.mark.asyncio
async def test_names_are_case_insensitive_reserved_safe_and_audited(api_client, core):
    client, db, settings = api_client
    connect(settings, core)
    user, _, _ = await banker(client, db, "DemoOwner")
    await banker(client, db, "DemoPlayer")
    conflict = await client.patch(
        "/api/v1/me/settings", json={"bank_username": "dEmoOwner"}, headers=ORIGIN
    )
    assert conflict.status_code == 409
    for name in ("admin", "SYSTEM", "hi", "abc def", "банк"):
        assert (
            await client.patch(
                "/api/v1/me/settings", json={"bank_username": name}, headers=ORIGIN
            )
        ).status_code == 422
    assert (
        db.execute(
            "SELECT bank_username FROM auth.application_users WHERE id=%s", (user,)
        ).fetchone()[0]
        == "DemoOwner"
    )
    assert (
        db.execute(
            "SELECT count(*) FROM auth.bank_username_history WHERE user_id=%s", (user,)
        ).fetchone()[0]
        >= 1
    )


@pytest.mark.asyncio
async def test_search_ambiguity_and_confirmed_minecraft_only(api_client, core):
    client, db, settings = api_client
    connect(settings, core)
    first, _, _ = await banker(client, db, "SharedName")
    second, _, _ = await banker(client, db, "OtherBanker")
    db.execute(
        "INSERT INTO auth.minecraft_verified_links(user_id,player_uuid,nickname,verified_at) VALUES(%s,%s,'SharedName',now())",
        (second, uuid.uuid4()),
    )
    db.commit()
    result = await client.get("/api/v1/recipients/search", params={"q": "SharedName"})
    assert result.status_code == 200, result.text
    assert {item["id"] for item in result.json()["items"]} == {first, second}
    assert (
        await client.get("/api/v1/recipients/search", params={"q": "Sh"})
    ).status_code == 422
    assert (
        await client.get("/api/v1/recipients/search", params={"q": "%"})
    ).status_code == 422
    assert not (
        await client.get("/api/v1/recipients/search", params={"q": "pending-nick"})
    ).json()["items"]
    db.execute(
        "INSERT INTO auth.recipient_lookup_windows(user_id,window_start,used) VALUES(%s,date_trunc('minute',now()),60) ON CONFLICT(user_id) DO UPDATE SET used=60,window_start=date_trunc('minute',now())",
        (second,),
    )
    db.commit()
    assert (
        await client.get("/api/v1/recipients/search", params={"q": "SharedName"})
    ).status_code == 429


@pytest.mark.asyncio
async def test_named_transfer_stays_bound_after_rename_and_repeated_confirmation(
    api_client, core
):
    client, db, settings = api_client
    connect(settings, core)
    sender, sender_token, source = await banker(client, db, "SenderName")
    receiver, receiver_token, target = await banker(client, db, "ReceiverName")
    await banker(client, db, "ThirdBanker")
    fund(core, db, sender, source["id"], "100.00")
    client.cookies.set("horizon_session", sender_token)
    quote = await client.post(
        "/api/v1/transfer-quotes",
        json={
            "recipient": {"kind": "bank_name", "value": "ReceiverName"},
            "amount": "25.50",
            "reason": "Спасибо за алмазы",
        },
        headers=ORIGIN,
    )
    assert quote.status_code == 201, quote.text
    quote = quote.json()
    client.cookies.set("horizon_session", receiver_token)
    assert (
        await client.patch(
            "/api/v1/me/settings",
            json={"bank_username": "RenamedReceiver"},
            headers=ORIGIN,
        )
    ).status_code == 200
    client.cookies.set("horizon_session", sender_token)
    headers = {**ORIGIN, "Idempotency-Key": quote["id"]}
    replies = await asyncio.gather(
        *(
            client.post(
                "/api/v1/transfer-quotes/" + quote["id"] + "/confirm", headers=headers
            )
            for _ in range(4)
        )
    )
    assert all(reply.status_code == 200 for reply in replies), [
        reply.text for reply in replies
    ]
    ids = {reply.json()["id"] for reply in replies}
    assert len(ids) == 1
    assert all(
        reply.json()["destination_account_id"] == target["id"] for reply in replies
    )
    assert (
        db.execute(
            "SELECT balance::text FROM finance.accounts WHERE id=%s", (target["id"],)
        ).fetchone()[0]
        == "25.50"
    )
    receipt = await client.get("/api/v1/receipts/" + next(iter(ids)))
    assert receipt.status_code == 200, receipt.text
    assert (
        receipt.json()["recipient"] == "ReceiverName"
    )  # Historical confirmation snapshot, not a redirected name.
    assert receipt.json()["reason"] == "Спасибо за алмазы"
    assert receipt.json()["fee"]["amount"] == "0.00"
    reconciled(db)


@pytest.mark.asyncio
async def test_foreign_source_quote_and_insufficient_balance_are_rejected(
    api_client, core
):
    client, db, settings = api_client
    connect(settings, core)
    first, first_token, source = await banker(client, db, "FirstBanker")
    _, second_token, _ = await banker(client, db, "SecondBanker")
    payload = {
        "recipient": {"kind": "bank_name", "value": "SecondBanker"},
        "amount": "1.00",
        "source_account_id": source["id"],
    }
    assert (
        await client.post("/api/v1/transfer-quotes", json=payload, headers=ORIGIN)
    ).status_code == 403
    client.cookies.set("horizon_session", first_token)
    assert (
        await client.post("/api/v1/transfer-quotes", json=payload, headers=ORIGIN)
    ).status_code == 409
    fund(core, db, first, source["id"], "5.00")
    quote = await client.post("/api/v1/transfer-quotes", json=payload, headers=ORIGIN)
    assert quote.status_code == 201, quote.text
    client.cookies.set("horizon_session", second_token)
    assert (
        await client.post(
            "/api/v1/transfer-quotes/" + quote.json()["id"] + "/confirm",
            headers={**ORIGIN, "Idempotency-Key": quote.json()["id"]},
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_expired_quote_recovers_lost_response_without_reexecuting_transfer(
    api_client, core
):
    from test_core_finance import request as money_request

    client, db, settings = api_client
    connect(settings, core)
    sender, token, source = await banker(client, db, "LostSender")
    _, _, target = await banker(client, db, "LostReceiver")
    fund(core, db, sender, source["id"], "10.00")
    client.cookies.set("horizon_session", token)
    quote = (
        await client.post(
            "/api/v1/transfer-quotes",
            json={
                "recipient": {"kind": "bank_name", "value": "LostReceiver"},
                "amount": "2.00",
            },
            headers=ORIGIN,
        )
    ).json()
    command = money_request(
        sender, source["id"], target["id"], "2.00", key="quote:" + quote["id"]
    )
    command.reason = ""
    operation = invoke(core.Transfer, command)
    db.execute(
        "UPDATE auth.transfer_quotes SET expires_at=now()-interval '1 minute' WHERE id=%s",
        (quote["id"],),
    )
    db.commit()
    result = await client.post(
        "/api/v1/transfer-quotes/" + quote["id"] + "/confirm",
        headers={**ORIGIN, "Idempotency-Key": quote["id"]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["id"] == operation.id
    assert (
        db.execute(
            "SELECT balance::text FROM finance.accounts WHERE id=%s", (target["id"],)
        ).fetchone()[0]
        == "2.00"
    )


@pytest.mark.asyncio
async def test_public_numbers_account_rename_primary_and_favorites(api_client, core):
    client, db, settings = api_client
    connect(settings, core)
    owner, token, primary = await banker(client, db, "AccountOwner")
    target_user, _, target = await banker(client, db, "FavoriteUser")
    client.cookies.set("horizon_session", token)
    key = str(uuid.uuid4())
    first = await client.post(
        "/api/v1/accounts",
        json={"name": "Личный резерв"},
        headers={**ORIGIN, "Idempotency-Key": key},
    )
    assert first.status_code == 201, first.text
    second = await client.post(
        "/api/v1/accounts",
        json={"name": "Личный резерв"},
        headers={**ORIGIN, "Idempotency-Key": key},
    )
    assert first.json()["id"] == second.json()["id"]
    changed = await client.patch(
        "/api/v1/accounts/" + first.json()["id"],
        json={"is_primary": True, "name": "Сбережения"},
        headers=ORIGIN,
    )
    assert changed.status_code == 200, changed.text
    assert (await client.get("/api/v1/dashboard")).json()["primary_account"][
        "id"
    ] == first.json()["id"]
    assert (
        await client.patch(
            "/api/v1/accounts/" + target["id"],
            json={"is_primary": True},
            headers=ORIGIN,
        )
    ).status_code == 403
    saved = await client.post(
        "/api/v1/recipients/saved",
        json={"recipient": {"kind": "user", "value": target_user}, "label": "Друг"},
        headers=ORIGIN,
    )
    assert saved.status_code == 201, saved.text
    db.execute(
        "UPDATE auth.application_users SET bank_username='RenamedFavorite' WHERE id=%s",
        (target_user,),
    )
    db.commit()
    recipients = (await client.get("/api/v1/recipients/saved")).json()
    assert recipients[0]["id"] == target_user
    assert recipients[0]["bank_username"] == "RenamedFavorite"
    assert primary["public_number"] != target["public_number"]
    assert (
        db.execute(
            "SELECT count(*) FROM finance.accounts WHERE owner_principal_id=%s",
            (owner,),
        ).fetchone()[0]
        == 2
    )


@pytest.mark.asyncio
async def test_organization_accounts_permissions_and_revocation(api_client, core):
    client, db, settings = api_client
    connect(settings, core)
    owner, owner_token, _ = await banker(client, db, "OrganizationOwner")
    member, member_token, _ = await banker(client, db, "OrganizationMember")
    client.cookies.set("horizon_session", owner_token)
    org = await client.post(
        "/api/v1/organizations",
        json={"name": "Пчёлка " + uuid.uuid4().hex[:6]},
        headers=ORIGIN,
    )
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]
    account_response = await client.post(
        f"/api/v1/organizations/{org_id}/accounts",
        json={"name": "Магазин Пчёлка"},
        headers={**ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert account_response.status_code == 201, account_response.text
    corporate = account_response.json()
    permissions = ["accounts.read", "balance.read", "history.read"]
    added = await client.put(
        f"/api/v1/organizations/{org_id}/members/{member}",
        json={"user_id": member, "permissions": permissions},
        headers=ORIGIN,
    )
    assert added.status_code == 200, added.text
    client.cookies.set("horizon_session", member_token)
    assert (
        await client.get(f"/api/v1/accounts/{corporate['id']}/balance")
    ).status_code == 200
    assert (
        await client.post(
            f"/api/v1/organizations/{org_id}/accounts",
            json={"name": "Несанкционированный"},
            headers={**ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
        )
    ).status_code == 403
    assert (
        await client.patch(
            f"/api/v1/accounts/{corporate['id']}",
            json={"is_primary": True},
            headers=ORIGIN,
        )
    ).status_code == 403
    client.cookies.set("horizon_session", owner_token)
    assert (
        await client.delete(
            f"/api/v1/organizations/{org_id}/members/{owner}", headers=ORIGIN
        )
    ).status_code == 403
    assert (
        await client.delete(
            f"/api/v1/organizations/{org_id}/members/{member}", headers=ORIGIN
        )
    ).status_code == 204
    client.cookies.set("horizon_session", member_token)
    assert (
        await client.get(f"/api/v1/accounts/{corporate['id']}/balance")
    ).status_code == 403
    assert (await client.get(f"/api/v1/organizations/{org_id}")).status_code == 403


@pytest.mark.asyncio
async def test_activity_notifications_and_safe_session_identifiers(api_client, core):
    client, db, settings = api_client
    connect(settings, core)
    owner, token, source = await banker(client, db, "NotificationOwner")
    fund(core, db, owner, source["id"], "8.00")
    events = await client.get("/api/v1/activity")
    assert events.status_code == 200, events.text
    assert events.json()["items"][0]["amount"]["amount"] == "8.00"
    notes = await client.get("/api/v1/notifications")
    assert notes.status_code == 200, notes.text
    assert notes.json()["unread"] >= 1
    assert any(n["operation_id"] for n in notes.json()["items"])
    assert (
        await client.post(
            "/api/v1/notifications/read", json={"all": True}, headers=ORIGIN
        )
    ).status_code == 204
    assert (await client.get("/api/v1/notifications")).json()["unread"] == 0
    extra_token = uuid.uuid4().hex + uuid.uuid4().hex
    db.execute(
        "INSERT INTO auth.user_sessions(token_hash,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",
        (hashlib.sha256(extra_token.encode()).hexdigest(), owner),
    )
    db.commit()
    sessions = await client.get("/api/v1/me/sessions")
    assert sessions.status_code == 200, sessions.text
    assert (
        token not in sessions.text
        and hashlib.sha256(token.encode()).hexdigest() not in sessions.text
    )
    other = next(item for item in sessions.json() if not item["current"])
    assert (
        await client.delete("/api/v1/me/sessions/" + other["id"], headers=ORIGIN)
    ).status_code == 204
    client.cookies.set("horizon_session", extra_token)
    assert (await client.get("/api/v1/me")).status_code == 401

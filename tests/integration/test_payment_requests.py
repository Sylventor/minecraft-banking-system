"""2.3: real PostgreSQL, C++ ledger and REST; no simulated financial transport."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import uuid
from datetime import UTC, datetime, timedelta

import grpc
import pytest
from pydantic import SecretStr
from test_api_security import api_client as api_fixture
from test_api_security import session_cookie
from test_core_finance import TOKEN, account, context, fund, invoke, pb, reconciled
from test_core_finance import core as core_fixture

api_client = api_fixture
core = core_fixture
ORIGIN = {"Origin": "http://localhost:8080"}


async def setup(api_client, core):
    client, db, settings = api_client
    settings.grpc_target = core.test_target
    settings.internal_grpc_token = SecretStr(TOKEN)
    merchant, mc = session_cookie(db)
    payer, pc = session_cookie(db)
    outsider, oc = session_cookie(db)
    client.cookies.set("horizon_session", mc)
    org = await client.post(
        "/api/v1/organizations",
        headers=ORIGIN,
        json={"name": "Shop " + uuid.uuid4().hex},
    )
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]
    result = await client.post(
        f"/api/v1/organizations/{org_id}/accounts",
        headers={**ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        json={"name": "Shop account"},
    )
    assert result.status_code == 201, result.text
    destination = result.json()["id"]
    source = account(core, payer)
    fund(core, db, payer, source, "500.00")
    return (
        client,
        db,
        merchant,
        mc,
        payer,
        pc,
        outsider,
        oc,
        org_id,
        destination,
        source,
    )


async def create(s, **changes):
    client, _, _, mc, payer, _, _, _, _, destination, _ = s
    client.cookies.set("horizon_session", mc)
    payload = {
        "payer_id": payer,
        "recipient_account_id": destination,
        "amount": "150.00",
        "description": "Shop purchase",
        "expires_in": 3600,
        **changes,
    }
    response = await client.post(
        "/api/v1/payment-requests",
        headers={**ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def pay_command(s, ident, key=None):
    return pb.PaymentRequestCommand(
        context=context(s[4]),
        action="pay",
        id=ident,
        source_account_id=s[10],
        confirmed=True,
        idempotency_key=key or uuid.uuid4().hex,
        fingerprint="b" * 64,
    )


@pytest.mark.asyncio
async def test_org_request_pay_real_ledger_and_replay_after_lost_response(
    api_client, core
):
    s = await setup(api_client, core)
    client, db = s[:2]
    item = await create(s, external_id="ORDER-123", metadata={"sku": "bee"})
    client.cookies.set("horizon_session", s[5])
    incoming = await client.get("/api/v1/payment-requests/incoming")
    assert any(x["id"] == item["id"] for x in incoming.json()["items"])
    assert (
        "fingerprint" not in item
        and "creation_key" not in item
        and "pay_key" not in item
    )
    # Core has committed, but the caller discards the response (backend crash / lost HTTP response).
    command = pay_command(s, item["id"])
    committed = json.loads(invoke(core.PaymentRequests, command).json)
    repeated = json.loads(invoke(core.PaymentRequests, command).json)
    assert committed["status"] == repeated["status"] == "paid"
    assert committed["transaction_id"] == repeated["transaction_id"]
    restored = (await client.get("/api/v1/payment-requests/" + item["id"])).json()
    assert (
        restored["status"] == "paid"
        and restored["transaction_id"] == committed["transaction_id"]
    )
    operation = db.execute(
        "SELECT amount::text,source_account_id::text,destination_account_id::text FROM finance.financial_operations WHERE id=%s",
        (committed["transaction_id"],),
    ).fetchone()
    assert operation == ("150.00", s[10], s[9])
    db.commit()
    assert (
        db.execute(
            "SELECT balance::text FROM finance.accounts WHERE id=%s", (s[10],)
        ).fetchone()[0]
        == "350.00"
    )
    db.commit()
    reconciled(db)


@pytest.mark.asyncio
async def test_rest_pay_confirmation_owned_account_and_final_states(api_client, core):
    s = await setup(api_client, core)
    client, db = s[:2]
    item = await create(s)
    url = f"/api/v1/payment-requests/{item['id']}/pay"
    key = uuid.uuid4().hex
    client.cookies.set("horizon_session", s[7])
    assert (
        await client.get(f"/api/v1/payment-requests/{item['id']}")
    ).status_code == 404
    assert (
        await client.post(
            url,
            headers={**ORIGIN, "Idempotency-Key": key},
            json={"source_account_id": s[10], "confirmed": True},
        )
    ).status_code == 403
    client.cookies.set("horizon_session", s[5])
    assert (
        await client.post(
            url,
            headers={**ORIGIN, "Idempotency-Key": key},
            json={"source_account_id": s[10], "confirmed": False},
        )
    ).status_code == 422
    foreign = account(core, s[6])
    assert (
        await client.post(
            url,
            headers={**ORIGIN, "Idempotency-Key": key},
            json={"source_account_id": foreign, "confirmed": True},
        )
    ).status_code == 403
    headers = {**ORIGIN, "Idempotency-Key": key}
    body = {"source_account_id": s[10], "confirmed": True}
    paid = await client.post(url, headers=headers, json=body)
    assert paid.status_code == 200, paid.text
    assert (await client.post(url, headers=headers, json=body)).json()[
        "transaction_id"
    ] == paid.json()["transaction_id"]
    assert (
        await client.post(
            url, headers={**ORIGIN, "Idempotency-Key": uuid.uuid4().hex}, json=body
        )
    ).status_code == 409
    assert (
        await client.patch(
            f"/api/v1/payment-requests/{item['id']}",
            headers=ORIGIN,
            json={"amount": "1.00"},
        )
    ).status_code == 405
    with pytest.raises(Exception):
        db.execute(
            "UPDATE finance.payment_requests SET amount=1 WHERE id=%s", (item["id"],)
        )
        db.commit()
    db.rollback()
    reconciled(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("same_key", [True, False])
async def test_concurrent_payments_only_one_debit(api_client, core, same_key):
    s = await setup(api_client, core)
    item = await create(s)
    key = uuid.uuid4().hex
    commands = [
        pay_command(s, item["id"], key),
        pay_command(s, item["id"], key if same_key else uuid.uuid4().hex),
    ]

    def run(command):
        try:
            return json.loads(invoke(core.PaymentRequests, command).json)
        except grpc.RpcError as error:
            return error.code()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, commands))
    paid = [r for r in results if isinstance(r, dict)]
    assert len(paid) == (2 if same_key else 1)
    assert len({r["transaction_id"] for r in paid}) == 1
    assert (
        s[1]
        .execute("SELECT balance::text FROM finance.accounts WHERE id=%s", (s[10],))
        .fetchone()[0]
        == "350.00"
    )
    s[1].commit()
    reconciled(s[1])


@pytest.mark.asyncio
async def test_merchant_keys_restricted_org_accounts_own_requests_and_revocation(
    api_client, core
):
    s = await setup(api_client, core)
    client, db = s[:2]
    key_body = {
        "name": "Shop integration",
        "organization_id": s[8],
        "account_ids": [s[9]],
        "scopes": [
            "payment_requests.create",
            "payment_requests.read",
            "payment_requests.cancel",
        ],
    }
    denied = await client.post(
        "/api/v1/api-keys",
        headers=ORIGIN,
        json={**key_body, "scopes": ["payments.create"]},
    )
    assert denied.status_code == 422
    created = await client.post("/api/v1/api-keys", headers=ORIGIN, json=key_body)
    assert created.status_code == 201, created.text
    key = created.json()
    headers = {
        "Authorization": "Bearer " + key["secret"],
        "Idempotency-Key": uuid.uuid4().hex,
    }
    payload = {
        "payer_id": s[4],
        "recipient_account_id": s[9],
        "amount": "25.00",
        "description": "Merchant purchase",
        "expires_in": 3600,
        "external_id": "ORDER-" + uuid.uuid4().hex,
    }
    request = await client.post(
        "/api/v1/payment-requests", headers=headers, json=payload
    )
    assert request.status_code == 201, request.text
    ident = request.json()["id"]
    replay = await client.post(
        "/api/v1/payment-requests", headers=headers, json=payload
    )
    assert replay.json()["id"] == ident
    mismatch = await client.post(
        "/api/v1/payment-requests", headers=headers, json={**payload, "amount": "26.00"}
    )
    assert mismatch.status_code == 409
    duplicate = await client.post(
        "/api/v1/payment-requests",
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
        json=payload,
    )
    assert duplicate.status_code == 409
    for url in (
        "/api/v1/me",
        "/api/v1/accounts",
        "/api/v1/admin/users",
        "/api/v1/organizations",
    ):
        assert (await client.get(url, headers=headers)).status_code == 403
    assert (
        await client.post(
            f"/api/v1/payment-requests/{ident}/pay",
            headers=headers,
            json={"source_account_id": s[10], "confirmed": True},
        )
    ).status_code == 403
    personal = account(core, s[2])
    assert (
        await client.post(
            "/api/v1/payment-requests",
            headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
            json={**payload, "recipient_account_id": personal},
        )
    ).status_code == 403
    session_request = await create(s)
    assert (
        await client.get(
            "/api/v1/payment-requests/" + session_request["id"], headers=headers
        )
    ).status_code == 404
    listed = (
        await client.get("/api/v1/payment-requests/outgoing", headers=headers)
    ).json()
    assert {x["id"] for x in listed["items"]} == {ident}
    assert (
        await client.post(f"/api/v1/payment-requests/{ident}/cancel", headers=headers)
    ).json()["status"] == "cancelled"
    client.cookies.set("horizon_session", s[3])
    assert (
        await client.delete("/api/v1/api-keys/" + key["id"], headers=ORIGIN)
    ).status_code == 204
    assert (
        await client.get("/api/v1/payment-requests/" + ident, headers=headers)
    ).status_code == 401
    db.commit()


@pytest.mark.asyncio
async def test_decline_cancel_expiry_and_notifications_survive_discord_outage(
    api_client, core
):
    from app.notifications import relay
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    s = await setup(api_client, core)
    client, db = s[:2]
    item = await create(s)
    client.cookies.set("horizon_session", s[5])
    assert (
        await client.post(
            f"/api/v1/payment-requests/{item['id']}/decline", headers=ORIGIN
        )
    ).json()["status"] == "declined"
    item = await create(s)
    assert (
        await client.post(
            f"/api/v1/payment-requests/{item['id']}/cancel", headers=ORIGIN
        )
    ).json()["status"] == "cancelled"
    item = await create(
        s,
        expires_in=None,
        expires_at=(datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
    )
    await asyncio.sleep(1.1)
    client.cookies.set("horizon_session", s[5])
    denied = await client.post(
        f"/api/v1/payment-requests/{item['id']}/pay",
        headers={**ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        json={"source_account_id": s[10], "confirmed": True},
    )
    assert denied.status_code == 409, denied.text
    assert (await client.get("/api/v1/payment-requests/" + item["id"])).json()[
        "status"
    ] == "expired"
    provider = client._transport.app.state.core_provider
    engine = create_async_engine(api_client[2].auth_database_url)
    async with async_sessionmaker(engine)() as session:
        # No Discord transport is running. Durable outbox persists independently.
        for _ in range(20):
            if await relay(session, await provider.get()) < 100:
                break
    await engine.dispose()
    payloads = db.execute(
        "SELECT payload FROM auth.discord_outbox WHERE user_id=%s AND payload ? 'payment_request_id'",
        (s[4],),
    ).fetchall()
    assert payloads
    assert any(p[0]["type"] == "payment_request.expired" for p in payloads)
    assert all(
        p[0]["url"].endswith("/payments/" + p[0]["payment_request_id"])
        for p in payloads
    )
    assert not any(p[0]["type"] == "payment_request.paid" for p in payloads)
    db.commit()


@pytest.mark.asyncio
async def test_insufficient_and_frozen_account_leave_request_pending(api_client, core):
    s = await setup(api_client, core)
    client, db = s[:2]
    item = await create(s, amount="600.00")
    with pytest.raises(grpc.RpcError) as error:
        invoke(core.PaymentRequests, pay_command(s, item["id"]))
    assert error.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    item = await create(s)
    db.execute("UPDATE finance.accounts SET status='frozen' WHERE id=%s", (s[9],))
    db.commit()
    with pytest.raises(grpc.RpcError) as error:
        invoke(core.PaymentRequests, pay_command(s, item["id"]))
    assert error.value.code() == grpc.StatusCode.ALREADY_EXISTS
    assert db.execute(
        "SELECT status,transaction_id FROM finance.payment_requests WHERE id=%s",
        (item["id"],),
    ).fetchone() == ("pending", None)
    assert (
        db.execute(
            "SELECT balance::text FROM finance.accounts WHERE id=%s", (s[10],)
        ).fetchone()[0]
        == "500.00"
    )
    db.commit()
    reconciled(db)


@pytest.mark.asyncio
async def test_expiry_during_wait_for_account_lock_prevents_debit(api_client, core):
    s = await setup(api_client, core)
    db = s[1]
    item = await create(
        s,
        expires_in=None,
        expires_at=(datetime.now(UTC) + timedelta(seconds=2)).isoformat(),
    )
    db.execute("SELECT id FROM finance.accounts WHERE id=%s FOR UPDATE", (s[9],))
    task = asyncio.create_task(
        asyncio.to_thread(invoke, core.PaymentRequests, pay_command(s, item["id"]))
    )
    await asyncio.sleep(2.2)
    db.commit()
    with pytest.raises(grpc.RpcError):
        await task
    assert (
        db.execute(
            "SELECT balance::text FROM finance.accounts WHERE id=%s", (s[10],)
        ).fetchone()[0]
        == "500.00"
    )
    db.commit()
    reconciled(db)


@pytest.mark.asyncio
async def test_foreign_organization_self_invoice_validation_and_rate_limit(
    api_client, core
):
    s = await setup(api_client, core)
    client = s[0]
    client.cookies.set("horizon_session", s[7])
    body = {
        "payer_id": s[4],
        "recipient_account_id": s[9],
        "amount": "1.00",
        "description": "Purchase",
        "expires_in": 3600,
    }

    async def post(payload):
        return await client.post(
            "/api/v1/payment-requests",
            headers={**ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
            json=payload,
        )

    assert (await post(body)).status_code == 403
    client.cookies.set("horizon_session", s[3])
    assert (await post({**body, "payer_id": s[2]})).status_code == 422
    for invalid in (
        {"amount": "0.00"},
        {"amount": 1.5},
        {"description": "x" * 501},
        {"expires_in": 0},
        {"expires_in": 2592001},
        {"status": "paid"},
    ):
        assert (await post({**body, **invalid})).status_code == 422
    for _ in range(20):
        assert (await post(body)).status_code == 201
    assert (await post(body)).status_code == 429


@pytest.mark.asyncio
async def test_active_request_cap_and_notification_preferences(api_client, core):
    from app.notifications import relay
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    s = await setup(api_client, core)
    client, db = s[:2]
    # Test-only fixture: proposals do not alter balances/ledger. The insert trigger
    # also exercises durable notifications for pending requests.
    db.execute(
        """INSERT INTO finance.payment_requests(creator_id,payer_id,recipient_account_id,organization_id,
        amount,description,expires_at,creation_key,fingerprint,created_at)
      SELECT %s,%s,%s,%s,1,'Active fixture',clock_timestamp()+interval '1 hour',
        'fixture-request-'||n,repeat('a',64),clock_timestamp()-interval '2 minutes'
      FROM generate_series(1,100) n""",
        (s[2], s[4], s[9], s[8]),
    )
    db.execute(
        "INSERT INTO auth.external_identities(id,user_id,provider,external_id) VALUES(%s,%s,'discord',%s)",
        (uuid.uuid4(), s[4], str(uuid.uuid4().int >> 64)),
    )
    db.execute(
        "INSERT INTO auth.notification_preferences(user_id,transfers_enabled) VALUES(%s,false) ON CONFLICT(user_id) DO UPDATE SET transfers_enabled=false",
        (s[4],),
    )
    db.commit()
    denied = await client.post(
        "/api/v1/payment-requests",
        headers={**ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "payer_id": s[4],
            "recipient_account_id": s[9],
            "amount": "1.00",
            "description": "Too many",
        },
    )
    assert denied.status_code == 429
    engine = create_async_engine(api_client[2].auth_database_url)
    provider = client._transport.app.state.core_provider
    async with async_sessionmaker(engine)() as session:
        for _ in range(40):
            if await relay(session, await provider.get()) < 100:
                break
    await engine.dispose()
    # Prior tests may leave other users' messages. Claim until this payer is processed.
    headers = {
        **ORIGIN,
        "X-Horizon-Bot-Token": api_client[
            2
        ].discord_bot_internal_token.get_secret_value(),
    }
    for _ in range(50):
        response = await client.post(
            "/api/v1/internal/notifications/claim", headers=headers, json={}
        )
        assert response.status_code == 200, response.text
        remaining = db.execute(
            "SELECT count(*) FROM auth.discord_outbox WHERE user_id=%s AND payload ? 'payment_request_id' AND status<>'suppressed'",
            (s[4],),
        ).fetchone()[0]
        db.commit()
        if not remaining:
            break
    states = db.execute(
        "SELECT status FROM auth.discord_outbox WHERE user_id=%s AND payload ? 'payment_request_id'",
        (s[4],),
    ).fetchall()
    assert states and all(row[0] == "suppressed" for row in states)
    db.commit()


@pytest.mark.asyncio
async def test_member_permissions_do_not_imply_access_to_all_org_requests(
    api_client, core
):
    s = await setup(api_client, core)
    client = s[0]
    item = await create(s)
    member_url = f"/api/v1/organizations/{s[8]}/members/{s[6]}"

    async def grants(permissions):
        client.cookies.set("horizon_session", s[3])
        result = await client.put(
            member_url,
            headers=ORIGIN,
            json={"user_id": s[6], "role": "member", "permissions": permissions},
        )
        assert result.status_code == 200, result.text
        client.cookies.set("horizon_session", s[7])

    await grants(["accounts.read", "balance.read"])
    assert (await client.get("/api/v1/payment-requests/outgoing")).json()["items"] == []
    assert (
        await client.get("/api/v1/payment-requests/" + item["id"])
    ).status_code == 404
    await grants(["payment_requests.read"])
    assert (
        await client.get("/api/v1/payment-requests/" + item["id"])
    ).status_code == 200
    assert (
        await client.post(
            f"/api/v1/payment-requests/{item['id']}/cancel", headers=ORIGIN
        )
    ).status_code == 403
    await grants(["payment_requests.create"])
    assert (
        await client.get("/api/v1/payment-requests/" + item["id"])
    ).status_code == 404
    body = {
        "payer_id": s[4],
        "recipient_account_id": s[9],
        "amount": "1.00",
        "description": "Member purchase",
    }
    created = await client.post(
        "/api/v1/payment-requests",
        headers={**ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        json=body,
    )
    assert created.status_code == 201, created.text
    own = created.json()["id"]
    assert (await client.get("/api/v1/payment-requests/" + own)).status_code == 200
    assert (
        await client.post(f"/api/v1/payment-requests/{own}/cancel", headers=ORIGIN)
    ).json()["status"] == "cancelled"
    await grants([])
    assert (await client.get("/api/v1/payment-requests/" + own)).status_code == 404


@pytest.mark.asyncio
async def test_personal_invoice_pagination_search_and_final_history(api_client, core):
    s = await setup(api_client, core)
    client = s[0]
    personal = account(core, s[2])
    items = []
    for n in range(3):
        items.append(
            await create(s, recipient_account_id=personal, external_id=f"PERSONAL-{n}")
        )
    listed = (
        await client.get(
            "/api/v1/payment-requests/outgoing?limit=2&order=asc&external_id=PERSONAL-"
        )
    ).json()
    assert [x["id"] for x in listed["items"]] == [
        x["id"] for x in items[:2]
    ] and listed["next_offset"] == 2
    assert listed["total"] == 3
    empty = (
        await client.get(
            "/api/v1/payment-requests/outgoing?offset=99&external_id=PERSONAL-"
        )
    ).json()
    assert empty["items"] == [] and empty["total"] == 3
    tail = (
        await client.get(
            "/api/v1/payment-requests/outgoing?limit=2&offset=2&order=asc&external_id=PERSONAL-"
        )
    ).json()
    assert [x["id"] for x in tail["items"]] == [items[2]["id"]] and tail[
        "next_offset"
    ] is None
    client.cookies.set("horizon_session", s[5])
    assert (
        await client.post(
            f"/api/v1/payment-requests/{items[0]['id']}/decline", headers=ORIGIN
        )
    ).status_code == 200
    history = (
        await client.get("/api/v1/payment-requests/history?status=declined")
    ).json()
    assert {x["id"] for x in history["items"]} == {items[0]["id"]}


@pytest.mark.asyncio
async def test_pay_cancel_race_has_exactly_one_final_outcome(api_client, core):
    s = await setup(api_client, core)
    item = await create(s)
    cancel = pb.PaymentRequestCommand(
        context=context(s[2]), action="cancel", id=item["id"]
    )

    def run(command):
        try:
            return json.loads(invoke(core.PaymentRequests, command).json)
        except grpc.RpcError as error:
            return error.code()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [pay_command(s, item["id"]), cancel]))
    final = (
        s[1]
        .execute(
            "SELECT status,transaction_id FROM finance.payment_requests WHERE id=%s",
            (item["id"],),
        )
        .fetchone()
    )
    balance = (
        s[1]
        .execute("SELECT balance::text FROM finance.accounts WHERE id=%s", (s[10],))
        .fetchone()[0]
    )
    assert (final[0], balance) in {("paid", "350.00"), ("cancelled", "500.00")}
    assert sum(isinstance(x, dict) for x in results) == 1
    s[1].commit()
    reconciled(s[1])

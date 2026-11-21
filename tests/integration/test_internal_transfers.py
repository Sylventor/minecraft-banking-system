"""Internal transfers use the existing quote -> real C++ ledger path."""

import asyncio
import uuid

import pytest
from test_api_security import api_client as api_fixture
from test_banking_experience import ORIGIN, banker, connect
from test_core_finance import (
    account,
    balance,
    context,
    fund,
    grant,
    invoke,
    pb,
    reconciled,
)
from test_core_finance import core as core_fixture

api_client = api_fixture
core = core_fixture


async def corporate(client):
    response = await client.post(
        "/api/v1/organizations",
        json={"name": "Internal " + uuid.uuid4().hex[:8]},
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text
    org = response.json()["id"]
    response = await client.post(
        f"/api/v1/organizations/{org}/accounts",
        json={"name": "Operating account"},
        headers={**ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 201, response.text
    return org, response.json()["id"]


async def quote(client, source, target, amount="12.34"):
    return await client.post(
        "/api/v1/transfer-quotes",
        json={
            "internal": True,
            "source_account_id": source,
            "recipient": {"kind": "account", "value": target},
            "amount": amount,
            "reason": "Move between accounts",
        },
        headers=ORIGIN,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_kind,target_kind",
    [
        ("personal", "personal"),
        ("personal", "organization"),
        ("organization", "personal"),
        ("organization", "organization"),
    ],
)
async def test_internal_transfer_exact_account_fee_and_concurrent_replay(
    api_client, core, source_kind, target_kind
):
    client, db, settings = api_client
    connect(settings, core)
    actor, _, primary = await banker(client, db, "Move" + uuid.uuid4().hex[:12])
    source = (
        primary["id"] if source_kind == "personal" else (await corporate(client))[1]
    )
    target = (
        account(core, actor)
        if target_kind == "personal"
        else (await corporate(client))[1]
    )
    fund(core, db, actor, source, "100.00")
    prepared = await quote(client, source, target)
    assert prepared.status_code == 201, prepared.text
    value = prepared.json()
    assert value["internal"] is True
    assert value["recipient"]["account_id"] == target
    assert value["fee"]["amount"] == "0.00" and value["total"]["amount"] == "12.34"
    assert balance(core, actor, source).balance.amount == "100.00"
    restored = await client.get("/api/v1/transfer-quotes/" + value["id"])
    assert restored.json()["internal"] is True
    replies = await asyncio.gather(
        *(
            client.post(
                f"/api/v1/transfer-quotes/{value['id']}/confirm",
                headers={**ORIGIN, "Idempotency-Key": value["id"]},
            )
            for _ in range(5)
        )
    )
    assert all(r.status_code == 200 for r in replies), [r.text for r in replies]
    assert len({r.json()["id"] for r in replies}) == 1
    assert replies[0].json()["destination_account_id"] == target
    assert balance(core, actor, source).balance.amount == "87.66"
    assert balance(core, actor, target).balance.amount == "12.34"
    db.execute(
        "UPDATE auth.transfer_quotes SET operation_id=NULL,expires_at=now()-interval '1 minute' WHERE id=%s",
        (value["id"],),
    )
    db.commit()
    recovered = await client.post(
        f"/api/v1/transfer-quotes/{value['id']}/confirm",
        headers={**ORIGIN, "Idempotency-Key": value["id"]},
    )
    assert (
        recovered.status_code == 200
        and recovered.json()["id"] == replies[0].json()["id"]
    )
    assert balance(core, actor, source).balance.amount == "87.66"
    reconciled(db)


@pytest.mark.asyncio
async def test_internal_rejects_same_or_foreign_personal_account_even_with_acl(
    api_client, core
):
    client, db, settings = api_client
    connect(settings, core)
    actor, token, source = await banker(client, db, "Private" + uuid.uuid4().hex[:10])
    _, _, foreign = await banker(client, db, "Foreign" + uuid.uuid4().hex[:10])
    client.cookies.set("horizon_session", token)
    fund(core, db, actor, source["id"], "20.00")
    assert (await quote(client, source["id"], source["id"])).status_code == 422
    assert (await quote(client, source["id"], foreign["id"])).status_code == 403
    for permission in ("accounts.read", "balance.read", "transfer"):
        grant(db, actor, foreign["id"], permission)
    assert (await quote(client, source["id"], foreign["id"])).status_code == 403
    fund(core, db, actor, foreign["id"], "20.00")
    assert (await quote(client, foreign["id"], source["id"])).status_code == 403
    ordinary = await client.post(
        "/api/v1/transfer-quotes",
        json={
            "source_account_id": source["id"],
            "recipient": {"kind": "account", "value": foreign["id"]},
            "amount": "1.00",
        },
        headers=ORIGIN,
    )
    assert ordinary.status_code == 201, ordinary.text
    reconciled(db)


@pytest.mark.asyncio
async def test_view_only_member_can_receive_but_cannot_debit_and_revocation_blocks_confirmation(
    api_client, core
):
    client, db, settings = api_client
    connect(settings, core)
    owner, owner_token, _ = await banker(client, db, "Owner" + uuid.uuid4().hex[:10])
    org, target = await corporate(client)
    member, member_token, personal = await banker(
        client, db, "Reader" + uuid.uuid4().hex[:10]
    )
    client.cookies.set("horizon_session", owner_token)
    added = await client.put(
        f"/api/v1/organizations/{org}/members/{member}",
        json={
            "user_id": member,
            "permissions": ["accounts.read", "balance.read", "history.read"],
        },
        headers=ORIGIN,
    )
    assert added.status_code == 200, added.text
    fund(core, db, member, personal["id"], "50.00")
    fund(core, db, owner, target, "30.00")
    client.cookies.set("horizon_session", member_token)
    receiving = await quote(client, personal["id"], target)
    assert receiving.status_code == 201, receiving.text
    assert (await quote(client, target, personal["id"])).status_code == 403
    client.cookies.set("horizon_session", owner_token)
    revoked = await client.delete(
        f"/api/v1/organizations/{org}/members/{member}", headers=ORIGIN
    )
    assert revoked.status_code == 204, revoked.text
    client.cookies.set("horizon_session", member_token)
    value = receiving.json()
    denied = await client.post(
        f"/api/v1/transfer-quotes/{value['id']}/confirm",
        headers={**ORIGIN, "Idempotency-Key": value["id"]},
    )
    assert denied.status_code == 403, denied.text
    assert balance(core, member, personal["id"]).balance.amount == "50.00"
    assert balance(core, owner, target).balance.amount == "30.00"
    reconciled(db)


@pytest.mark.asyncio
async def test_internal_requires_both_api_key_resources_and_respects_reserves(
    api_client, core
):
    client, db, settings = api_client
    connect(settings, core)
    actor, _, source = await banker(client, db, "Scoped" + uuid.uuid4().hex[:10])
    target = account(core, actor)
    fund(core, db, actor, source["id"], "10.00")
    invoke(
        core.CreateHold,
        pb.HoldRequest(
            context=context(actor),
            account_id=source["id"],
            amount=pb.Money(amount="4.00", currency="DB"),
            idempotency_key=uuid.uuid4().hex,
            request_fingerprint="c" * 64,
        ),
    )
    assert balance(core, actor, source["id"]).available.amount == "6.00"
    assert (await quote(client, source["id"], target, "6.01")).status_code == 409
    created = await client.post(
        "/api/v1/api-keys",
        headers=ORIGIN,
        json={
            "name": "Source only",
            "scopes": ["transfers.create"],
            "account_ids": [source["id"]],
        },
    )
    assert created.status_code == 201, created.text
    response = await client.post(
        "/api/v1/transfer-quotes",
        headers={"Authorization": "Bearer " + created.json()["secret"]},
        json={
            "internal": True,
            "source_account_id": source["id"],
            "recipient": {"kind": "account", "value": target},
            "amount": "1.00",
        },
    )
    assert response.status_code == 403, response.text
    reconciled(db)


@pytest.mark.asyncio
async def test_internal_core_rechecks_revoked_source_debit_mandate(api_client, core):
    client, db, settings = api_client
    connect(settings, core)
    actor, _, personal = await banker(client, db, "Revoke" + uuid.uuid4().hex[:10])
    _, source = await corporate(client)
    fund(core, db, actor, source, "50.00")
    prepared = await quote(client, source, personal["id"])
    assert prepared.status_code == 201, prepared.text
    db.execute(
        "UPDATE finance.account_permissions SET revoked_at=now() WHERE account_id=%s AND principal_id=%s AND permission='transfer'",
        (source, actor),
    )
    db.commit()
    value = prepared.json()
    denied = await client.post(
        f"/api/v1/transfer-quotes/{value['id']}/confirm",
        headers={**ORIGIN, "Idempotency-Key": value["id"]},
    )
    assert denied.status_code == 403, denied.text
    assert balance(core, actor, source).balance.amount == "50.00"
    assert balance(core, actor, personal["id"]).balance.amount == "0.00"
    reconciled(db)

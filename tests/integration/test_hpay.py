"""Real PostgreSQL and Core coverage for HPay settlement and authorization."""

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import grpc
import pytest
from test_api_security import api_client, session_cookie
from test_atm import call, runtime
from test_banking_experience import connect
from test_core_finance import balance, context, fund, invoke, pb, reconciled
from test_core_finance import core as core_fixture

core = core_fixture


def shop(core, owner=None):
    owner, org = owner or str(uuid.uuid4()), str(uuid.uuid4())
    ctx = context(owner)
    ctx.source = "rest"
    invoke(
        core.EnsureOrganization,
        pb.OrganizationRequest(
            context=ctx,
            organization_id=org,
            owner_id=owner,
            name="Test shop",
        ),
    )
    account = invoke(
        core.CreateOrganizationAccount,
        pb.OrganizationAccountRequest(
            context=ctx,
            organization_id=org,
            name="Sales",
            idempotency_key=str(uuid.uuid4()),
        ),
    ).id
    return owner, org, account


@pytest.mark.asyncio
async def test_hpay_rest_catalogue_and_internal_confirmation_boundary(api_client, core):
    client, database, settings = api_client
    connect(settings, core)
    owner, token = session_cookie(database)
    _, org, recipient = shop(core, owner)
    base = f"/api/v1/organizations/{org}/hpay/products"
    assert (await client.get(base)).status_code == 401
    client.cookies.set("horizon_session", token)
    created = await client.post(
        base,
        headers={"Origin": "http://localhost:8080"},
        json={
            "name": "Pickaxe",
            "account_id": recipient,
            "price": "5.25",
        },
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["code"] == "H-" + item["id"]
    assert (await client.get(f"{base}/{item['id']}")).json()["price"] == "5.25"
    assert (
        await client.post(
            "/api/v1/internal/atm/hpay/confirm",
            headers={"Origin": "http://localhost:8080"},
            json={
                "nickname": "Buyer",
                "instance_id": str(uuid.uuid4()),
                "player_uuid": str(uuid.uuid4()),
                "purchase_id": str(uuid.uuid4()),
                "product_id": item["id"],
            },
        )
    ).status_code == 401
    _, outsider = session_cookie(database)
    client.cookies.set("horizon_session", outsider)
    assert (await client.get(base)).status_code == 403


def catalogue(core, actor, org, action, body=None, product=""):
    ctx = context(actor)
    ctx.source = "rest"
    return json.loads(
        invoke(
            core.Business,
            pb.BusinessRequest(
                context=ctx,
                organization_id=org,
                action="hpay." + action,
                payload=json.dumps(body or {}),
                target_id=product,
            ),
        ).json
    )


def test_hpay_snapshot_concurrent_confirm_and_cooldown(core, database):
    instance, buyer, _, payer_account, _ = runtime(core, database)
    owner, org, recipient = shop(core)
    fund(core, database, buyer, payer_account, "10.00")
    item = {
        "name": "Pickaxe",
        "account_id": recipient,
        "price": "5.25",
        "status": "active",
    }
    product = catalogue(core, owner, org, "create", item)["items"][0]["id"]
    data = {"instance_id": instance, "user_id": buyer, "product_id": product}
    quote = call(core, "hpay.quote", data)
    assert quote["status"] == "pending"
    assert call(core, "hpay.quote", data)["id"] == quote["id"]
    assert balance(core, buyer, payer_account).balance.amount == "10.00"

    with pytest.raises(grpc.RpcError) as forbidden:
        catalogue(core, buyer, org, "list")
    assert forbidden.value.code() == grpc.StatusCode.PERMISSION_DENIED
    with pytest.raises(grpc.RpcError) as foreign:
        call(
            core, "hpay.confirm", {**data, "user_id": owner, "purchase_id": quote["id"]}
        )
    assert foreign.value.code() == grpc.StatusCode.NOT_FOUND

    ctx = context(owner)
    other_account = invoke(
        core.CreateOrganizationAccount,
        pb.OrganizationAccountRequest(
            context=ctx,
            organization_id=org,
            name="Other sales",
            idempotency_key=str(uuid.uuid4()),
        ),
    ).id
    catalogue(
        core,
        owner,
        org,
        "update",
        {**item, "price": "9.99", "name": "New name", "account_id": other_account},
        product,
    )
    time.sleep(0.26)
    confirmation = {**data, "purchase_id": quote["id"], "protection_seconds": 3}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: call(core, "hpay.confirm", confirmation), range(4))
        )
    assert len({result["operation_id"] for result in results}) == 1
    assert all(
        result["status"] == "completed" and result["price"] == "5.25"
        for result in results
    )
    assert results[0]["product_name"] == "Pickaxe"
    assert call(core, "hpay.quote", data)["operation_id"] == results[0]["operation_id"]
    assert call(core, "hpay.quote", data)["operation_id"] == results[0]["operation_id"]
    assert balance(core, buyer, payer_account).balance.amount == "4.75"
    assert balance(core, owner, recipient).balance.amount == "5.25"
    assert balance(core, owner, other_account).balance.amount == "0.00"
    sales = catalogue(core, owner, org, "sales")
    assert sales["total"] == "5.25" and sales["count"] == "1"
    assert sales["items"][0]["operation_id"] == results[0]["operation_id"]
    database.execute(
        "UPDATE finance.hpay_purchases SET protected_until=clock_timestamp() WHERE id=%s",
        (quote["id"],),
    )
    database.commit()
    later = call(core, "hpay.quote", data)
    assert later["status"] == "pending" and later["id"] != quote["id"]
    assert later["price"] == "9.99"
    reconciled(database)


def test_hpay_switch_cancel_disable_expire_and_insufficient_funds(core, database):
    instance, buyer, _, payer_account, _ = runtime(core, database)
    owner, org, recipient = shop(core)
    item = {
        "name": "Pickaxe",
        "account_id": recipient,
        "price": "2.00",
        "status": "active",
    }
    products = [
        catalogue(core, owner, org, "create", item)["items"][0]["id"] for _ in range(2)
    ]
    common = {"instance_id": instance, "user_id": buyer}
    first = {**common, "product_id": products[0]}
    second = {**common, "product_id": products[1]}
    previous = call(core, "hpay.quote", first)
    current = call(core, "hpay.quote", second)
    assert (
        call(core, "hpay.confirm", {**first, "purchase_id": previous["id"]})["status"]
        == "cancelled"
    )
    catalogue(core, owner, org, "update", {**item, "status": "disabled"}, products[1])
    catalogue(core, owner, org, "update", item, products[1])
    time.sleep(0.26)
    assert (
        call(core, "hpay.confirm", {**second, "purchase_id": current["id"]})["status"]
        == "failed"
    )

    pending = call(core, "hpay.quote", first)
    database.execute(
        "UPDATE finance.hpay_purchases SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
        (pending["id"],),
    )
    database.commit()
    assert (
        call(core, "hpay.confirm", {**first, "purchase_id": pending["id"]})["status"]
        == "expired"
    )

    pending = call(core, "hpay.quote", second)
    time.sleep(0.26)
    with pytest.raises(grpc.RpcError) as insufficient:
        call(core, "hpay.confirm", {**second, "purchase_id": pending["id"]})
    assert insufficient.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert call(core, "hpay.cancel", common)["status"] == "cancelled"
    pending = call(core, "hpay.quote", first)
    database.execute(
        "UPDATE finance.accounts SET status='closed' WHERE id=%s", (recipient,)
    )
    database.commit()
    time.sleep(0.26)
    assert (
        call(core, "hpay.confirm", {**first, "purchase_id": pending["id"]})["status"]
        == "failed"
    )
    assert balance(core, buyer, payer_account).balance.amount == "0.00"
    assert balance(core, owner, recipient).balance.amount == "0.00"
    reconciled(database)

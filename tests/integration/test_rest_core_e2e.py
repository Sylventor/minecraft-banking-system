from __future__ import annotations

import uuid

import pytest
from pydantic import SecretStr
from test_api_security import api_client as api_fixture
from test_api_security import session_cookie
from test_core_finance import TOKEN, authority, grant, reconciled
from test_core_finance import core as core_fixture

api_client = api_fixture
core = core_fixture


@pytest.mark.asyncio
async def test_rest_to_cpp_to_postgres_financial_journey(api_client, core):
    client, db, settings = api_client
    settings.grpc_target = core.test_target
    settings.internal_grpc_token = SecretStr(TOKEN)
    actor, cookie = session_cookie(db)
    client.cookies.set("horizon_session", cookie)
    headers = {"Origin": "http://localhost:8080"}
    source = await client.post(
        "/api/v1/accounts", json={"account_type": "personal"}, headers=headers
    )
    target = await client.post(
        "/api/v1/accounts", json={"account_type": "personal"}, headers=headers
    )
    assert source.status_code == 201, source.text
    assert target.status_code == 201, target.text
    src = source.json()["id"]
    dst = target.json()["id"]
    authority(db, actor, "issue.create")
    authority(db, actor, "refund.create")
    grant(db, actor, src, "issue.create")
    grant(db, actor, dst, "refund.create")
    for permission in ("admin.issuance", "admin.refunds"):
        db.execute(
            "INSERT INTO auth.user_permissions(user_id,permission,granted_by,reason) VALUES(%s,%s,%s,'test grant')",
            (actor, permission, actor),
        )
    db.commit()
    issuance = await client.post(
        "/api/v1/admin/issuances",
        json={
            "target_account_id": src,
            "amount": "100.00",
            "reason": "Initial test allocation",
            "authority_reference": "test mandate",
        },
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert issuance.status_code == 201, issuance.text
    key = uuid.uuid4().hex
    payload = {
        "source_account_id": src,
        "destination_account_id": dst,
        "amount": "25.00",
        "reason": "Test payment",
    }
    transfer = await client.post(
        "/api/v1/payments", json=payload, headers={**headers, "Idempotency-Key": key}
    )
    assert transfer.status_code == 201, transfer.text
    replay = await client.post(
        "/api/v1/payments", json=payload, headers={**headers, "Idempotency-Key": key}
    )
    assert replay.json()["id"] == transfer.json()["id"]
    assert (await client.get(f"/api/v1/accounts/{src}/balance")).json()["available"][
        "amount"
    ] == "75.00"
    history = (await client.get(f"/api/v1/accounts/{src}/operations")).json()["items"]
    assert transfer.json()["id"] in {item["id"] for item in history}
    refund = await client.post(
        "/api/v1/admin/refunds",
        json={
            "source_account_id": dst,
            "original_operation_id": transfer.json()["id"],
            "amount": "5.00",
            "reason": "Authorized partial refund",
        },
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert refund.status_code == 201, refund.text
    assert (await client.get(f"/api/v1/accounts/{src}/balance")).json()["available"][
        "amount"
    ] == "80.00"
    created = await client.post(
        "/api/v1/api-keys",
        json={
            "name": "Test integration",
            "scopes": ["balance.read"],
            "account_ids": [src],
        },
        headers=headers,
    )
    assert created.status_code == 201
    client.cookies.clear()
    auth = {"Authorization": "Bearer " + created.json()["secret"]}
    assert (
        await client.get(f"/api/v1/accounts/{src}/balance", headers=auth)
    ).status_code == 200
    assert (
        await client.get(f"/api/v1/accounts/{dst}/balance", headers=auth)
    ).status_code == 403
    client.cookies.set("horizon_session", cookie)
    assert (
        await client.delete("/api/v1/api-keys/" + created.json()["id"], headers=headers)
    ).status_code == 204
    client.cookies.clear()
    assert (
        await client.get(f"/api/v1/accounts/{src}/balance", headers=auth)
    ).status_code == 401
    assert (
        db.execute(
            "SELECT COUNT(*) FROM auth.audit_events WHERE actor_id=%s AND action IN ('money.issue','money.refund')",
            (actor,),
        ).fetchone()[0]
        == 2
    )
    reconciled(db)

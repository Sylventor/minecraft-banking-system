"""Owner self approval and non-owner separation against the real core and DB."""

from datetime import UTC, datetime

import pytest
from psycopg.errors import CheckViolation
from test_api_security import api_client as api_fixture
from test_business import action, batch, path, payroll_setup
from test_core_finance import account
from test_core_finance import core as core_fixture
from test_exchange import command, market_setup
from test_payment_requests import ORIGIN

api_client = api_fixture
core = core_fixture


@pytest.mark.asyncio
async def test_owner_can_create_approve_and_receive_payroll(api_client, core):
    s, _ = await payroll_setup(api_client, core)
    client, db = s[:2]
    client.cookies.set("horizon_session", s[7])
    personal = account(core, s[2])
    employee = await client.post(
        path(s, "payroll/employees"),
        headers=ORIGIN,
        json={"user_id": s[2], "account_id": personal, "salary": "50.00"},
    )
    assert employee.status_code == 201, employee.text
    client.cookies.set("horizon_session", s[3])
    b = await batch(s, employee.json())
    assert (await action(s, b, "submit")).status_code == 200
    approved = await action(s, b, "approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_by"] == s[2]
    paid = await action(s, b, "execute")
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "completed"
    assert (await action(s, b, "execute")).json()["items"] == paid.json()["items"]
    db.commit()


@pytest.mark.asyncio
async def test_nonowner_author_and_recipient_still_need_another_approver(
    api_client, core
):
    s, employee = await payroll_setup(api_client, core)
    client, db = s[:2]
    client.cookies.set("horizon_session", s[7])
    b = await batch(s, employee)
    assert (await action(s, b, "submit")).status_code == 200
    assert (await action(s, b, "approve")).status_code == 403
    with pytest.raises(CheckViolation), db.transaction():
        db.execute(
            "UPDATE finance.payroll_batches SET approved_by=created_by WHERE id=%s",
            (b["id"],),
        )
    db.commit()
    client.cookies.set("horizon_session", s[3])
    added = await client.put(
        path(s, f"members/{s[4]}"),
        headers=ORIGIN,
        json={"user_id": s[4], "role": "administrator"},
    )
    assert added.status_code == 200, added.text
    client.cookies.set("horizon_session", s[5])
    assert (await action(s, b, "approve")).status_code == 403
    client.cookies.set("horizon_session", s[3])
    assert (await action(s, b, "approve")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [True, False])
async def test_schedule_self_approval_and_worker_follow_ownership(
    api_client, core, owner
):
    from app.payroll_worker import tick

    s, employee = await payroll_setup(api_client, core)
    client, db = s[:2]
    client.cookies.set("horizon_session", s[3] if owner else s[7])
    created = await client.post(
        path(s, "payroll/schedules"),
        headers=ORIGIN,
        json={
            "source_account_id": s[9],
            "description": "Owner approval",
            "name": "Weekly",
            "items": [{"employee_id": employee["id"]}],
            "frequency": "weekly",
            "weekday": 1,
            "month_day": 1,
            "local_time": "12:00",
            "timezone": "Europe/Prague",
            "mode": "automatic",
            "max_amount": "200.00",
            "confirmed": True,
        },
    )
    assert created.status_code == 201, created.text
    schedule = created.json()
    url = path(s, f"payroll/schedules/{schedule['id']}/approve")
    body = {"confirmed": True, "revision": schedule["revision"]}
    response = await client.post(url, headers=ORIGIN, json=body)
    assert response.status_code == (200 if owner else 403), response.text
    if not owner:
        with pytest.raises(CheckViolation), db.transaction():
            db.execute(
                "UPDATE finance.payroll_schedules SET approved_by=created_by WHERE id=%s",
                (schedule["id"],),
            )
        db.commit()
        client.cookies.set("horizon_session", s[3])
        response = await client.post(url, headers=ORIGIN, json=body)
        assert response.status_code == 200, response.text
    assert response.json()["approved_by"] == s[2]
    db.execute(
        "UPDATE finance.payroll_schedules SET next_run_at=clock_timestamp()-interval '1 day' WHERE id=%s",
        (schedule["id"],),
    )
    db.commit()
    await tick(await client._transport.app.state.core_provider.get())
    assert db.execute(
        "SELECT status,approved_by::text FROM finance.payroll_batches WHERE schedule_id=%s",
        (schedule["id"],),
    ).fetchone() == ("completed", s[2])
    db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [True, False])
async def test_dividend_self_approval_is_reserved_for_the_owner(
    api_client, core, owner
):
    s, stock, _, _ = await market_setup(api_client, core)
    client, db = s[:2]
    client.cookies.set("horizon_session", s[3])
    added = await client.put(
        f"/api/v1/organizations/{s[8]}/members/{s[6]}",
        headers=ORIGIN,
        json={"user_id": s[6], "role": "administrator"},
    )
    assert added.status_code == 200, added.text
    client.cookies.set("horizon_session", s[3] if owner else s[7])
    moment = datetime.now(UTC).isoformat()
    created = await command(
        s,
        "exchange/dividends",
        {
            "stock_id": stock,
            "account_id": s[9],
            "mode": "total",
            "amount": "10.01",
            "record_at": moment,
            "pay_at": moment,
        },
    )
    assert created.status_code == 201, created.text
    ident = created.json()["id"]
    response = await command(s, f"exchange/dividends/{ident}/approve", {})
    assert response.status_code == (200 if owner else 403), response.text
    if not owner:
        with pytest.raises(CheckViolation), db.transaction():
            db.execute(
                "UPDATE finance.exchange_dividends SET approved_by=created_by WHERE id=%s",
                (ident,),
            )
        db.commit()
        client.cookies.set("horizon_session", s[3])
        response = await command(s, f"exchange/dividends/{ident}/approve", {})
        assert response.status_code == 200, response.text
    assert response.json()["approved_by"] == s[2]
    paid = await command(s, f"exchange/dividends/{ident}/execute", {})
    assert paid.status_code == 200 and paid.json()["status"] == "completed", paid.text

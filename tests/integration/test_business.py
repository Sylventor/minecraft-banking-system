"""Business 2.4 against real PostgreSQL and native C++ gRPC; no Minecraft calls."""

import asyncio
import concurrent.futures
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import grpc
import pytest
from test_api_security import api_client as api_fixture
from test_api_security import session_cookie
from test_core_finance import (
    TOKEN,
    account,
    authority,
    context,
    fund,
    grant,
    invoke,
    pb,
    reconciled,
    request,
)
from test_core_finance import core as core_fixture
from test_payment_requests import ORIGIN, setup

api_client = api_fixture
core = core_fixture


def path(s, suffix):
    return f"/api/v1/organizations/{s[8]}/" + suffix


async def payroll_setup(api_client, core):
    s = await setup(api_client, core)
    client, db = s[:2]
    added = await client.put(
        path(s, f"members/{s[6]}"),
        headers=ORIGIN,
        json={"user_id": s[6], "role": "administrator"},
    )
    assert added.status_code == 200, added.text
    fund(core, db, s[2], s[9], "1000.00")
    emp = await client.post(
        path(s, "payroll/employees"),
        headers=ORIGIN,
        json={
            "user_id": s[4],
            "account_id": s[10],
            "salary": "100.00",
            "position": "Engineer",
        },
    )
    assert emp.status_code == 201, emp.text
    return s, emp.json()


async def batch(s, employee):
    result = await s[0].post(
        path(s, "payroll/batches"),
        headers={**ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "source_account_id": s[9],
            "description": "Зарплата за октябрь",
            "items": [{"employee_id": employee["id"]}],
        },
    )
    assert result.status_code == 201, result.text
    return result.json()


async def action(s, batch, act):
    result = await s[0].post(
        path(s, f"payroll/batches/{batch['id']}/{act}"),
        headers=ORIGIN,
        json={"confirmed": True},
    )
    return result


async def approved(s, employee):
    b = await batch(s, employee)
    assert (await action(s, b, "submit")).status_code == 200
    s[0].cookies.set("horizon_session", s[7])
    result = await action(s, b, "approve")
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.asyncio
async def test_analytics_ledger_internal_refund_opening_balance_csv_and_scope(
    api_client, core
):
    s = await setup(api_client, core)
    client, db = s[:2]
    fund(core, db, s[2], s[9], "1000.00")
    cutoff = datetime.now(UTC)
    second = await client.post(
        path(s, "accounts"),
        headers={**ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        json={"name": "Second"},
    )
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]
    received = invoke(core.Transfer, request(s[4], s[10], s[9], "100.00"))
    outgoing = request(s[2], s[9], s[10], "50.00")
    outgoing.reason = "=1+2"
    paid = invoke(core.Transfer, outgoing)
    invoke(core.Transfer, request(s[2], s[9], second_id, "200.00"))
    authority(db, s[2], "refund.create")
    grant(db, s[2], s[9], "refund.create")
    reverse = request(s[2], s[9], s[10], "20.00")
    reverse.related_operation_id = received.id
    invoke(core.Refund, reverse)
    params = {
        "from": cutoff.isoformat(),
        "to": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        "timezone": "Europe/Prague",
    }
    summary = await client.get(path(s, "analytics/summary"), params=params)
    assert summary.status_code == 200, summary.text
    value = summary.json()
    assert value["balance"] == "1030.00"
    assert (
        value["current"]["incoming"] == "100.00"
        and value["current"]["outgoing"] == "70.00"
    )
    assert value["current"]["net_cashflow"] == "30.00"
    single = await client.get(
        path(s, "analytics/summary"), params={**params, "account_id": s[9]}
    )
    assert single.status_code == 200, single.text
    assert single.json()["current"]["net_cashflow"] == "-170.00"
    assert single.json()["balance"] == "830.00"
    page1 = await client.get(
        path(s, "analytics/transactions"), params={**params, "limit": 1}
    )
    page2 = await client.get(
        path(s, "analytics/transactions"),
        params={**params, "limit": 1, "cursor": page1.json()["next_cursor"]},
    )
    assert page1.json()["items"][0]["id"] != page2.json()["items"][0]["id"]
    empty = await client.get(
        path(s, "analytics/summary"),
        params={
            "from": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "to": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        },
    )
    assert empty.status_code == 200, empty.text
    assert (
        empty.json()["current"]["incoming"] == "0.00"
        and empty.json()["current"]["incoming_count"] == 0
    )
    assert (
        await client.get(
            path(s, "analytics/summary"), params={"from": cutoff.isoformat()}
        )
    ).status_code == 422
    history = await client.get(path(s, "analytics/balance-history"), params=params)
    assert history.status_code == 200, history.text
    assert (
        history.json()["items"][-1]["balance"] == "1030.00"
    )  # opening 1000, not merely incoming 100
    category = await client.put(
        path(s, f"analytics/transactions/{paid.id}/category"),
        headers=ORIGIN,
        json={"category": "rent"},
    )
    assert category.status_code == 200, category.text
    cats = (await client.get(path(s, "analytics/categories"), params=params)).json()
    assert next(x for x in cats["items"] if x["key"] == "rent")["outgoing"] == "50.00"
    csv = await client.get(
        path(s, "analytics/export"),
        params={**params, "export_type": "transactions", "limit": 100},
    )
    assert csv.status_code == 200, csv.text
    assert "'=1+2" in csv.text and csv.text.startswith("\ufeff")
    counterparties = await client.get(
        path(s, "analytics/counterparties"), params=params
    )
    assert counterparties.status_code == 200, counterparties.text
    assert all("balance" not in x for x in counterparties.json()["items"])
    key = await client.post(
        "/api/v1/api-keys",
        headers=ORIGIN,
        json={
            "name": "Analytics",
            "scopes": ["analytics.read", "analytics.export"],
            "account_ids": [s[9]],
        },
    )
    assert key.status_code == 201, key.text
    bearer = {"Authorization": "Bearer " + key.json()["secret"]}
    restricted = await client.get(
        path(s, "analytics/summary"), params=params, headers=bearer
    )
    assert restricted.status_code == 200, restricted.text
    assert restricted.json()["account_count"] == 1
    assert (
        await client.get(
            path(s, "analytics/summary"),
            params={**params, "account_id": second_id},
            headers=bearer,
        )
    ).status_code == 403
    assert (
        await client.get(path(s, "payroll/employees"), headers=bearer)
    ).status_code == 403
    client.cookies.set("horizon_session", s[7])
    assert (
        await client.get(path(s, "analytics/summary"), params=params)
    ).status_code == 403
    db.commit()
    reconciled(db)


@pytest.mark.asyncio
async def test_process_crash_between_items_recovers_without_repaying(api_client, core):
    """Kill a separate native core while its second item is blocked in PostgreSQL."""
    from app.payroll_worker import tick
    from horizon.banking.v1 import banking_pb2_grpc as rpc

    s, e = await payroll_setup(api_client, core)
    client, db = s[:2]
    user, _ = session_cookie(db)
    destination = account(core, user)
    added = await client.post(
        path(s, "payroll/employees"),
        headers=ORIGIN,
        json={"user_id": user, "account_id": destination, "salary": "50.00"},
    )
    assert added.status_code == 201, added.text
    created = await client.post(
        path(s, "payroll/batches"),
        headers={**ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "source_account_id": s[9],
            "description": "Crash recovery",
            "items": [{"employee_id": e["id"]}, {"employee_id": added.json()["id"]}],
        },
    )
    assert created.status_code == 201, created.text
    b = created.json()
    assert (await action(s, b, "submit")).status_code == 200
    client.cookies.set("horizon_session", s[7])
    assert (await action(s, b, "approve")).status_code == 200
    ordered = db.execute(
        "SELECT id::text,destination_account_id FROM finance.payroll_items WHERE batch_id=%s ORDER BY id",
        (b["id"],),
    ).fetchall()
    db.commit()
    root = Path(__file__).resolve().parents[2]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {
        **os.environ,
        "FINANCE_DATABASE_URL": os.environ["TEST_FINANCE_DATABASE_URL"],
        "GRPC_BIND_ADDRESS": f"127.0.0.1:{port}",
        "INTERNAL_GRPC_TOKEN": TOKEN,
        "HORIZON_INITIAL_ISSUANCE_ACCOUNT_ID": "00000000-0000-0000-0000-000000000001",
    }
    env["PATH"] = (
        str(root / ".tools/vcpkg-installed/x64-windows-release/bin")
        + os.pathsep
        + env.get("PATH", "")
    )
    process = subprocess.Popen(
        [os.environ["CORE_EXECUTABLE"]],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        grpc.channel_ready_future(channel).result(timeout=20)
        stub = rpc.BankingServiceStub(channel)
        # This read-only row lock holds back the second transfer; the first must
        # commit normally through Move/Record. No test SQL fabricates money.
        db.execute(
            "SELECT id FROM finance.accounts WHERE id=%s FOR UPDATE", (ordered[1][1],)
        )
        cmd = pb.BusinessRequest(
            context=context(s[6]),
            organization_id=s[8],
            action="batch.execute",
            target_id=b["id"],
            payload='{"confirmed":true}',
        )
        pending = pool.submit(invoke, stub.Business, cmd)
        # Poll on a separate connection; db above intentionally holds one lock.
        import psycopg

        with psycopg.connect(
            os.environ["TEST_DATABASE_URL"], autocommit=True
        ) as observer:
            for _ in range(250):
                first = observer.execute(
                    "SELECT operation_id::text FROM finance.payroll_items WHERE id=%s",
                    (ordered[0][0],),
                ).fetchone()[0]
                if first:
                    break
                time.sleep(0.02)
            assert first, (
                "first real payroll item did not commit before simulated crash"
            )
        process.kill()
        process.wait(timeout=10)
        db.rollback()
        with pytest.raises(grpc.RpcError):
            pending.result(timeout=10)
    finally:
        db.rollback()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        channel.close()
        pool.shutdown(wait=True)
    actual_core = await client._transport.app.state.core_provider.get()
    await tick(actual_core)
    recovered = await client.get(path(s, "payroll/batches/" + b["id"]))
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["status"] == "completed"
    assert (
        next(i for i in recovered.json()["items"] if i["id"] == ordered[0][0])[
            "operation_id"
        ]
        == first
    )
    assert (
        db.execute(
            "SELECT count(DISTINCT operation_id) FROM finance.payroll_items WHERE batch_id=%s",
            (b["id"],),
        ).fetchone()[0]
        == 2
    )
    db.commit()
    reconciled(db)


@pytest.mark.asyncio
async def test_payroll_create_approve_pay_replay_category_and_privacy(api_client, core):
    s, e = await payroll_setup(api_client, core)
    client, db = s[:2]
    b = await approved(s, e)
    result = await action(s, b, "execute")
    assert result.status_code == 200, result.text
    result = result.json()
    assert (
        result["status"] == "completed" and result["items"][0]["status"] == "completed"
    )
    op = result["items"][0]["operation_id"]
    repeat = await action(s, b, "execute")
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["items"][0]["operation_id"] == op
    overview = await client.get(path(s, "payroll/overview"))
    assert overview.status_code == 200, overview.text
    assert overview.json()["paid_last30"] == "100.00"
    history = await client.get(path(s, "payroll/history"), params={"limit": 1})
    assert history.json()["items"][0]["operation_id"] == op
    csv = await client.get(
        path(s, "analytics/export"), params={"export_type": "payroll"}
    )
    assert csv.status_code == 200 and op in csv.text
    filtered = await client.get(
        path(s, "analytics/export"),
        params={"export_type": "payroll", "category": "rent"},
    )
    assert filtered.status_code == 200 and op not in filtered.text
    assert (
        db.execute(
            "SELECT balance::text FROM finance.accounts WHERE id=%s", (s[10],)
        ).fetchone()[0]
        == "600.00"
    )
    assert (
        db.execute(
            "SELECT category_key FROM finance.operation_categories WHERE operation_id=%s",
            (op,),
        ).fetchone()[0]
        == "payroll"
    )
    assert (
        db.execute(
            "SELECT count(*) FROM finance.event_outbox WHERE principal_id=%s AND payload->>'type'='payroll.salary' AND payload->>'operation_id'=%s",
            (s[4], op),
        ).fetchone()[0]
        == 1
    )
    assert (
        db.execute(
            "SELECT count(*) FROM finance.event_outbox WHERE payload->>'operation_id'=%s AND payload->>'type'='transfer'",
            (op,),
        ).fetchone()[0]
        == 0
    )
    db.commit()
    client.cookies.set("horizon_session", s[5])
    assert (await client.get(path(s, "payroll/employees"))).status_code == 403
    assert (await client.get(path(s, "payroll/batches/" + b["id"]))).status_code == 403
    reconciled(db)


@pytest.mark.asyncio
async def test_salary_self_escalation_invalid_account_and_read_only_member(
    api_client, core
):
    s, e = await payroll_setup(api_client, core)
    client, db = s[:2]
    # A salary recipient needs no administrative membership. Even after explicit
    # delegation of HR powers, changing their own salary or per-batch amount fails.
    rights = [
        "payroll.read",
        "payroll.employees.manage",
        "payroll.salaries.manage",
        "payroll.batches.create",
    ]
    invited = await client.put(
        path(s, f"members/{s[4]}"),
        headers=ORIGIN,
        json={"user_id": s[4], "role": "member", "permissions": rights},
    )
    assert invited.status_code == 200, invited.text
    client.cookies.set("horizon_session", s[5])
    assert (
        await client.patch(
            path(s, "payroll/employees/" + e["id"]),
            headers=ORIGIN,
            json={"salary": "200.00"},
        )
    ).status_code == 403
    override = await client.post(
        path(s, "payroll/batches"),
        headers={**ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "source_account_id": s[9],
            "description": "Self raise",
            "items": [{"employee_id": e["id"], "amount": "200.00"}],
        },
    )
    assert override.status_code == 403, override.text
    client.cookies.set("horizon_session", s[3])
    foreign = await client.post(
        path(s, "payroll/batches"),
        headers={**ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "source_account_id": s[10],
            "description": "Foreign source",
            "items": [{"employee_id": e["id"]}],
        },
    )
    assert foreign.status_code == 409, foreign.text
    malformed = await client.patch(
        path(s, "payroll/employees/" + e["id"]),
        headers=ORIGIN,
        json={"note": "bad\x00text"},
    )
    assert malformed.status_code == 422
    readonly = await client.put(
        path(s, f"members/{s[4]}"),
        headers=ORIGIN,
        json={"user_id": s[4], "role": "member", "permissions": ["payroll.read"]},
    )
    assert readonly.status_code == 200, readonly.text
    client.cookies.set("horizon_session", s[5])
    assert (await client.get(path(s, "payroll/employees"))).status_code == 200
    assert (
        await client.patch(
            path(s, "payroll/employees/" + e["id"]),
            headers=ORIGIN,
            json={"active": False},
        )
    ).status_code == 403
    # No recipient, lacking payroll.read, receives another employee's batch notices.
    db.commit()
    reconciled(db)


@pytest.mark.asyncio
async def test_manual_schedule_waits_for_approval_and_revoked_auto_mandate_stops(
    api_client, core
):
    from app.payroll_worker import ACTOR, call, tick

    s, e = await payroll_setup(api_client, core)
    client, db = s[:2]
    body = {
        "source_account_id": s[9],
        "description": "Monthly",
        "name": "Monthly",
        "items": [{"employee_id": e["id"]}],
        "frequency": "monthly",
        "month_day": 31,
        "local_time": "02:30",
        "timezone": "Europe/Prague",
        "mode": "manual",
        "max_amount": "100.00",
        "confirmed": True,
    }
    too_small = await client.post(
        path(s, "payroll/schedules"),
        headers=ORIGIN,
        json={**body, "max_amount": "99.00"},
    )
    assert too_small.status_code == 422, too_small.text
    manual = await client.post(path(s, "payroll/schedules"), headers=ORIGIN, json=body)
    assert manual.status_code == 201, manual.text
    m = manual.json()
    db.execute(
        "UPDATE finance.payroll_schedules SET next_run_at=clock_timestamp()-interval '1 hour' WHERE id=%s",
        (m["id"],),
    )
    db.commit()
    actual_core = await client._transport.app.state.core_provider.get()
    await tick(actual_core)
    row = db.execute(
        "SELECT status,approved_by FROM finance.payroll_batches WHERE schedule_id=%s",
        (m["id"],),
    ).fetchone()
    assert row == ("pending_approval", None)
    assert (
        db.execute(
            "SELECT balance::text FROM finance.accounts WHERE id=%s", (s[9],)
        ).fetchone()[0]
        == "1000.00"
    )
    db.commit()
    # Create a separately approved automatic occurrence, then revoke its mandate
    # between durable creation and execution (the worker's crash boundary).
    automatic = await client.post(
        path(s, "payroll/schedules"),
        headers=ORIGIN,
        json={**body, "mode": "automatic", "name": "Automatic"},
    )
    assert automatic.status_code == 201, automatic.text
    a = automatic.json()
    client.cookies.set("horizon_session", s[7])
    assert (
        await client.post(
            path(s, "payroll/schedules/" + a["id"] + "/approve"),
            headers=ORIGIN,
            json={"confirmed": True, "revision": a["revision"]},
        )
    ).status_code == 200
    due = datetime.now(UTC) - timedelta(hours=1)
    db.execute(
        "UPDATE finance.payroll_schedules SET next_run_at=%s WHERE id=%s",
        (due, a["id"]),
    )
    db.commit()
    occurrence = await call(
        actual_core,
        ACTOR,
        s[8],
        "schedule.fire",
        a["id"],
        {
            "scheduled_for": due.isoformat(),
            "next_run_at": (due + timedelta(days=31)).isoformat(),
        },
    )
    db.execute(
        "UPDATE finance.organization_members SET permissions=array_remove(permissions,'payroll.automatic.manage') WHERE organization_id=%s AND principal_id=%s",
        (s[8], s[6]),
    )
    db.commit()
    await tick(actual_core)
    assert (
        db.execute(
            "SELECT status FROM finance.payroll_schedules WHERE id=%s", (a["id"],)
        ).fetchone()[0]
        == "paused"
    )
    assert (
        db.execute(
            "SELECT count(*) FROM finance.payroll_items WHERE batch_id=%s AND operation_id IS NOT NULL",
            (occurrence["batch_id"],),
        ).fetchone()[0]
        == 0
    )
    db.commit()
    reconciled(db)


@pytest.mark.asyncio
async def test_concurrent_batch_execution_and_lost_response(api_client, core):
    s, e = await payroll_setup(api_client, core)
    b = await approved(s, e)
    cmd = pb.BusinessRequest(
        context=context(s[6]),
        organization_id=s[8],
        action="batch.execute",
        target_id=b["id"],
        payload='{"confirmed":true}',
    )

    def run(_):
        return json.loads(invoke(core.Business, cmd).json)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert all(x["status"] == "completed" for x in results)
    assert (
        results[0]["items"][0]["operation_id"] == results[1]["items"][0]["operation_id"]
    )
    # Discard those replies: a restored HTTP client reads durable per-item evidence.
    restored = await s[0].get(path(s, "payroll/batches/" + b["id"]))
    assert (
        restored.json()["items"][0]["operation_id"]
        == results[0]["items"][0]["operation_id"]
    )
    s[1].commit()
    reconciled(s[1])


@pytest.mark.asyncio
async def test_insufficient_balance_starts_no_payments_and_revocation_denies(
    api_client, core
):
    s, e = await payroll_setup(api_client, core)
    client, db = s[:2]
    edited = await client.patch(
        path(s, "payroll/employees/" + e["id"]),
        headers=ORIGIN,
        json={"salary": "1100.00"},
    )
    assert edited.status_code == 200, edited.text
    b = await approved(s, edited.json())
    denied = await action(s, b, "execute")
    assert denied.status_code == 409, denied.text
    assert (
        db.execute(
            "SELECT count(*) FROM finance.payroll_items WHERE batch_id=%s AND operation_id IS NOT NULL",
            (b["id"],),
        ).fetchone()[0]
        == 0
    )
    db.execute(
        "UPDATE finance.organization_members SET permissions=array_remove(permissions,'payroll.execute') WHERE organization_id=%s AND principal_id=%s",
        (s[8], s[6]),
    )
    db.commit()
    assert (await action(s, b, "execute")).status_code == 403


@pytest.mark.asyncio
async def test_partial_failure_retries_only_failed_item(api_client, core):
    s, e = await payroll_setup(api_client, core)
    client, db = s[:2]
    user, cookie = session_cookie(db)
    dest = account(core, user)
    second = await client.post(
        path(s, "payroll/employees"),
        headers=ORIGIN,
        json={"user_id": user, "account_id": dest, "salary": "50.00"},
    )
    assert second.status_code == 201, second.text
    b = await client.post(
        path(s, "payroll/batches"),
        headers={**ORIGIN, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "source_account_id": s[9],
            "description": "Salary",
            "items": [{"employee_id": e["id"]}, {"employee_id": second.json()["id"]}],
        },
    )
    b = b.json()
    assert (await action(s, b, "submit")).status_code == 200
    client.cookies.set("horizon_session", s[7])
    assert (await action(s, b, "approve")).status_code == 200
    db.execute("UPDATE finance.accounts SET status='frozen' WHERE id=%s", (dest,))
    db.commit()
    result = await action(s, b, "execute")
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "partially_completed"
    successes = [
        x["operation_id"] for x in result.json()["items"] if x["status"] == "completed"
    ]
    db.execute("UPDATE finance.accounts SET status='active' WHERE id=%s", (dest,))
    db.commit()
    retry = await action(s, b, "retry")
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "completed"
    assert all(
        x in [i["operation_id"] for i in retry.json()["items"]] for x in successes
    )
    assert (
        db.execute(
            "SELECT balance::text FROM finance.accounts WHERE id=%s", (s[10],)
        ).fetchone()[0]
        == "600.00"
    )
    db.commit()
    reconciled(db)


@pytest.mark.asyncio
async def test_schedule_approval_deduplication_and_employee_change_revokes_mandate(
    api_client, core
):
    from app.payroll_worker import tick

    s, e = await payroll_setup(api_client, core)
    client, db = s[:2]
    data = {
        "source_account_id": s[9],
        "description": "Weekly salary",
        "name": "Weekly",
        "items": [{"employee_id": e["id"]}],
        "frequency": "weekly",
        "weekday": 1,
        "month_day": 1,
        "local_time": "12:00",
        "timezone": "Europe/Prague",
        "mode": "automatic",
        "max_amount": "200.00",
        "confirmed": True,
    }
    result = await client.post(path(s, "payroll/schedules"), headers=ORIGIN, json=data)
    assert result.status_code == 201, result.text
    sched = result.json()
    url = path(s, "payroll/schedules/" + sched["id"])
    assert (
        await client.post(
            url + "/approve",
            headers=ORIGIN,
            json={"confirmed": True, "revision": sched["revision"]},
        )
    ).status_code == 200
    client.cookies.set("horizon_session", s[7])
    approval = await client.post(
        url + "/approve",
        headers=ORIGIN,
        json={"confirmed": True, "revision": sched["revision"]},
    )
    assert approval.status_code == 200, approval.text
    db.execute(
        "UPDATE finance.payroll_schedules SET next_run_at=clock_timestamp()-interval '1 day' WHERE id=%s",
        (sched["id"],),
    )
    db.commit()
    actual_core = await client._transport.app.state.core_provider.get()
    await asyncio.gather(tick(actual_core), tick(actual_core))
    b = db.execute(
        "SELECT status FROM finance.payroll_batches WHERE schedule_id=%s",
        (sched["id"],),
    ).fetchall()
    assert b == [("completed",)]
    db.commit()
    client.cookies.set("horizon_session", s[3])
    change = await client.patch(
        path(s, "payroll/employees/" + e["id"]),
        headers=ORIGIN,
        json={"salary": "150.00"},
    )
    assert change.status_code == 200, change.text
    paused = (await client.get(url)).json()
    assert paused["status"] == "paused" and paused["approved_by"] is None
    assert (
        await client.patch(url, headers=ORIGIN, json={"status": "active"})
    ).status_code == 403
    assert (
        db.execute(
            "SELECT count(*) FROM finance.payroll_batches WHERE schedule_id=%s",
            (sched["id"],),
        ).fetchone()[0]
        == 1
    )
    db.commit()
    reconciled(db)

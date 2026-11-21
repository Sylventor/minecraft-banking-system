from __future__ import annotations

import concurrent.futures
import time
import uuid
from datetime import UTC, datetime, timedelta

import grpc
import pytest
from test_core_finance import account, balance, fund, invoke, request
from test_core_finance import core as core_fixture

core = core_fixture


def test_deadline_is_checked_after_idempotency_lock_not_before(core, database):
    actor = str(uuid.uuid4())
    source, destination = account(core, actor), account(core, actor)
    fund(core, database, actor, source, "5.00")
    key = uuid.uuid4().hex
    command = request(
        actor,
        source,
        destination,
        "1.00",
        key=key,
        not_after=(datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
    )
    database.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (actor + ":transfer:" + key,),
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(invoke, core.Transfer, command)
        time.sleep(1.2)
        assert not pending.done()
        database.commit()
        with pytest.raises(grpc.RpcError) as error:
            pending.result(timeout=10)
        assert error.value.code() == grpc.StatusCode.ALREADY_EXISTS
    assert balance(core, actor, destination).balance.amount == "0.00"
    assert (
        database.execute(
            "SELECT count(*) FROM finance.idempotency_records WHERE scope_id=%s AND idempotency_key=%s",
            (actor, key),
        ).fetchone()[0]
        == 0
    )


def test_completed_retry_is_still_available_after_deadline(core, database):
    actor = str(uuid.uuid4())
    source, destination = account(core, actor), account(core, actor)
    fund(core, database, actor, source, "5.00")
    command = request(
        actor,
        source,
        destination,
        "1.00",
        not_after=(datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
    )
    original = invoke(core.Transfer, command)
    time.sleep(1.2)
    assert invoke(core.Transfer, command).id == original.id
    assert balance(core, actor, destination).balance.amount == "1.00"

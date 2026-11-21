from __future__ import annotations

import concurrent.futures
import os
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import grpc
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/app/grpc/generated"))
from horizon.banking.v1 import banking_pb2 as pb  # noqa: E402
from horizon.banking.v1 import banking_pb2_grpc as rpc  # noqa: E402

TOKEN = "test-core-internal-token-" + "x" * 32
ISSUER = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(scope="module")
def core():
    executable = os.environ.get("CORE_EXECUTABLE")
    if not executable:
        pytest.skip("CORE_EXECUTABLE is required for real C++/PostgreSQL tests")
    opts = conninfo_to_dict(
        os.environ.get("TEST_FINANCE_DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    )
    if not opts["dbname"].startswith("horizon_test_"):
        pytest.fail("isolated database required")
    opts["user"] = "horizon_finance"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {
        **os.environ,
        "GRPC_BIND_ADDRESS": f"127.0.0.1:{port}",
        "FINANCE_DATABASE_URL": make_conninfo(**opts),
        "INTERNAL_GRPC_TOKEN": TOKEN,
        "HORIZON_INITIAL_ISSUANCE_ACCOUNT_ID": ISSUER,
    }
    env["PATH"] = (
        str(ROOT / ".tools/vcpkg-installed/x64-windows-release/bin")
        + os.pathsep
        + env.get("PATH", "")
    )
    process = subprocess.Popen(
        [executable],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        grpc.channel_ready_future(channel).result(timeout=20)
        stub = rpc.BankingServiceStub(channel)
        stub.test_target = f"127.0.0.1:{port}"
        yield stub
    finally:
        channel.close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def context(actor: str):
    return pb.RequestContext(
        actor_id=actor,
        request_id=str(uuid.uuid4()),
        correlation_id="test",
        source="integration_test",
    )


def invoke(method, request):
    return method(request, metadata=(("x-horizon-internal-token", TOKEN),), timeout=10)


def account(core, actor: str) -> str:
    return invoke(
        core.CreateAccount,
        pb.CreateAccountRequest(context=context(actor), account_type="personal"),
    ).id


def grant(database, actor: str, account_id: str, permission: str):
    database.execute(
        "INSERT INTO finance.account_permissions(account_id,principal_id,permission,granted_by) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (account_id, actor, permission, actor),
    )
    database.commit()


def authority(database, actor: str, permission: str):
    database.execute(
        "INSERT INTO finance.authorities(principal_id,permission,granted_by,reason) VALUES(%s,%s,%s,'integration test') ON CONFLICT DO NOTHING",
        (actor, permission, actor),
    )
    database.commit()


def request(
    actor: str,
    source: str,
    destination: str,
    amount: str,
    key: str | None = None,
    **kwargs,
):
    return pb.OperationRequest(
        context=context(actor),
        source_account_id=source,
        destination_account_id=destination,
        amount=pb.Money(amount=amount, currency="DB"),
        idempotency_key=key or uuid.uuid4().hex,
        request_fingerprint="a" * 64,
        reason="integration test",
        **kwargs,
    )


def fund(core, database, actor: str, account_id: str, amount: str):
    authority(database, actor, "issue.create")
    grant(database, actor, account_id, "issue.create")
    return invoke(
        core.Issue,
        request(
            actor, ISSUER, account_id, amount, authority_reference="test authority"
        ),
    )


def balance(core, actor: str, account_id: str):
    return invoke(
        core.GetBalance,
        pb.GetBalanceRequest(context=context(actor), account_id=account_id),
    )


def reconciled(database):
    assert (
        database.execute(
            "SELECT COUNT(*) FROM finance.reconciliation WHERE balance<>ledger_balance OR held<>active_holds"
        ).fetchone()[0]
        == 0
    )


def test_transfer_duplicate_and_changed_payload(core, database):
    actor = str(uuid.uuid4())
    source = account(core, actor)
    target = account(core, actor)
    fund(core, database, actor, source, "10.00")
    command = request(actor, source, target, "2.00")
    first = invoke(core.Transfer, command)
    second = invoke(core.Transfer, command)
    assert first.id == second.id
    command.amount.amount = "3.00"
    with pytest.raises(grpc.RpcError) as error:
        invoke(core.Transfer, command)
    assert error.value.code() == grpc.StatusCode.ALREADY_EXISTS
    assert balance(core, actor, source).available.amount == "8.00"
    assert balance(core, actor, target).available.amount == "2.00"
    reconciled(database)


def test_parallel_transfers_cannot_overspend(core, database):
    actor = str(uuid.uuid4())
    source = account(core, actor)
    target = account(core, actor)
    fund(core, database, actor, source, "10.00")

    def transfer(_):
        try:
            invoke(core.Transfer, request(actor, source, target, "1.00"))
            return True
        except grpc.RpcError as error:
            assert error.code() == grpc.StatusCode.FAILED_PRECONDITION
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(transfer, range(25)))
    assert sum(outcomes) == 10
    assert balance(core, actor, source).available.amount == "0.00"
    assert balance(core, actor, target).available.amount == "10.00"
    reconciled(database)


def test_parallel_same_key_creates_one_operation(core, database):
    actor = str(uuid.uuid4())
    source = account(core, actor)
    target = account(core, actor)
    fund(core, database, actor, source, "10.00")
    command = request(actor, source, target, "1.00")
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(
            pool.map(lambda _: invoke(core.Transfer, command).id, range(24))
        )
    assert len(set(outcomes)) == 1
    assert balance(core, actor, target).balance.amount == "1.00"
    reconciled(database)


def test_refunds_serialize_against_original_and_validate_actual_debit_account(
    core, database
):
    actor = str(uuid.uuid4())
    source = account(core, actor)
    target = account(core, actor)
    fund(core, database, actor, source, "10.00")
    original = invoke(core.CreatePayment, request(actor, source, target, "10.00"))
    authority(database, actor, "refund.create")
    grant(database, actor, target, "refund.create")

    def refund(_):
        try:
            invoke(
                core.Refund,
                request(actor, target, "", "7.00", related_operation_id=original.id),
            )
            return True
        except grpc.RpcError as error:
            assert error.code() == grpc.StatusCode.ALREADY_EXISTS
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(refund, range(2)))
    assert sum(outcomes) == 1
    assert balance(core, actor, target).available.amount == "3.00"
    with pytest.raises(grpc.RpcError):
        invoke(
            core.Refund,
            request(actor, source, "", "1.00", related_operation_id=original.id),
        )
    reconciled(database)


def test_hold_capture_and_release_are_replay_safe(core, database):
    actor = str(uuid.uuid4())
    source = account(core, actor)
    target = account(core, actor)
    fund(core, database, actor, source, "10.00")
    hold = invoke(
        core.CreateHold,
        pb.HoldRequest(
            context=context(actor),
            account_id=source,
            amount=pb.Money(amount="6.00", currency="DB"),
            idempotency_key=uuid.uuid4().hex,
            request_fingerprint="b" * 64,
        ),
    )
    assert hold.hold_id
    assert balance(core, actor, source).available.amount == "4.00"
    capture = pb.HoldActionRequest(
        context=context(actor),
        hold_id=hold.hold_id,
        destination_account_id=target,
        idempotency_key=uuid.uuid4().hex,
        request_fingerprint="c" * 64,
    )
    result = invoke(core.CaptureHold, capture)
    assert invoke(core.CaptureHold, capture).id == result.id
    assert balance(core, actor, target).balance.amount == "6.00"
    assert balance(core, actor, source).held.amount == "0.00"
    hold2 = invoke(
        core.CreateHold,
        pb.HoldRequest(
            context=context(actor),
            account_id=source,
            amount=pb.Money(amount="1.00", currency="DB"),
            idempotency_key=uuid.uuid4().hex,
            request_fingerprint="d" * 64,
        ),
    )
    release = pb.HoldActionRequest(
        context=context(actor),
        hold_id=hold2.hold_id,
        idempotency_key=uuid.uuid4().hex,
        request_fingerprint="e" * 64,
    )
    assert invoke(core.ReleaseHold, release).id == invoke(core.ReleaseHold, release).id
    assert balance(core, actor, source).available.amount == "4.00"
    reconciled(database)


def test_issuance_repeats_and_core_denies_unprivileged_actor(core, database):
    actor = str(uuid.uuid4())
    target = account(core, actor)
    fund(core, database, actor, target, "1.00")
    fund(core, database, actor, target, "1.00")
    assert balance(core, actor, target).balance.amount == "2.00"
    with pytest.raises(grpc.RpcError) as error:
        invoke(
            core.Issue,
            request(
                str(uuid.uuid4()),
                ISSUER,
                target,
                "1.00",
                authority_reference="untrusted",
            ),
        )
    assert error.value.code() == grpc.StatusCode.PERMISSION_DENIED
    reconciled(database)


def test_core_does_not_disclose_foreign_account_and_history_paginates(core, database):
    actor = str(uuid.uuid4())
    target = account(core, actor)
    for _ in range(5):
        fund(core, database, actor, target, "1.00")
    with pytest.raises(grpc.RpcError) as error:
        balance(core, str(uuid.uuid4()), target)
    assert error.value.code() == grpc.StatusCode.PERMISSION_DENIED
    first = invoke(
        core.GetHistory,
        pb.HistoryRequest(context=context(actor), account_id=target, limit=2),
    )
    second = invoke(
        core.GetHistory,
        pb.HistoryRequest(
            context=context(actor), account_id=target, limit=2, cursor=first.next_cursor
        ),
    )
    assert len(first.operations) == 2 and len(second.operations) == 2
    assert not {o.id for o in first.operations} & {o.id for o in second.operations}


def test_failure_after_balance_updates_rolls_back_every_record(core, database):
    actor = str(uuid.uuid4())
    source = account(core, actor)
    target = account(core, actor)
    fund(core, database, actor, source, "10.00")
    command = request(actor, source, target, "2.00")
    database.execute(
        sql.SQL("""CREATE FUNCTION finance.inject_test_failure() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN IF NEW.request_id={} THEN RAISE EXCEPTION 'injected integration failure'; END IF; RETURN NEW; END $$""").format(
            sql.Literal(command.context.request_id)
        )
    )
    database.execute(
        "CREATE TRIGGER inject_test_failure AFTER INSERT ON finance.financial_operations FOR EACH ROW EXECUTE FUNCTION finance.inject_test_failure()"
    )
    database.commit()
    try:
        with pytest.raises(grpc.RpcError) as error:
            invoke(core.Transfer, command)
        assert error.value.code() == grpc.StatusCode.INTERNAL
        assert balance(core, actor, source).available.amount == "10.00"
        assert balance(core, actor, target).available.amount == "0.00"
        assert (
            database.execute(
                "SELECT COUNT(*) FROM finance.idempotency_records WHERE idempotency_key=%s",
                (command.idempotency_key,),
            ).fetchone()[0]
            == 0
        )
    finally:
        database.execute(
            "DROP TRIGGER inject_test_failure ON finance.financial_operations"
        )
        database.execute("DROP FUNCTION finance.inject_test_failure()")
        database.commit()
    assert invoke(core.Transfer, command).status == "completed"
    reconciled(database)


def test_revocation_committed_before_authorization_prevents_transfer(core, database):
    actor = str(uuid.uuid4())
    source = account(core, actor)
    target = account(core, actor)
    fund(core, database, actor, source, "10.00")
    database.execute(
        "UPDATE finance.account_permissions SET revoked_at=now() WHERE account_id=%s AND principal_id=%s AND permission='transfer'",
        (source, actor),
    )
    # Keep the permission row locked until the core has been dispatched.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            invoke, core.Transfer, request(actor, source, target, "1.00")
        )
        database.commit()
        with pytest.raises(grpc.RpcError) as error:
            pending.result(timeout=10)
    assert error.value.code() == grpc.StatusCode.PERMISSION_DENIED
    assert balance(core, actor, source).balance.amount == "10.00"
    reconciled(database)


def test_core_checks_server_owned_limit_inside_transaction(core, database):
    actor = str(uuid.uuid4())
    source = account(core, actor)
    target = account(core, actor)
    fund(core, database, actor, source, "10.00")
    old = database.execute(
        "SELECT max_operation FROM finance.policy WHERE id=1"
    ).fetchone()[0]
    database.execute("UPDATE finance.policy SET max_operation=0.50 WHERE id=1")
    database.commit()
    try:
        with pytest.raises(grpc.RpcError) as error:
            invoke(core.Transfer, request(actor, source, target, "1.00"))
        assert error.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
        assert balance(core, actor, source).available.amount == "10.00"
    finally:
        database.execute(
            "UPDATE finance.policy SET max_operation=%s WHERE id=1", (old,)
        )
        database.commit()
    reconciled(database)

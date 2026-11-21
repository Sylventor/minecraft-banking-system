"""Restore a real custom-format dump into a new test database and reconcile it."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from test_core_finance import account, context, fund, invoke, pb, request
from test_core_finance import core as core_fixture

core = core_fixture


def executable(name: str) -> str:
    root = Path(__file__).resolve().parents[2]
    portable = root / ".tools/postgresql/pgsql/bin" / (name + ".exe")
    if sys.platform == "win32" and portable.exists():
        return str(portable)
    found = shutil.which(name)
    if not found:
        raise RuntimeError(name + " is required for a full restore test")
    return found


def test_dump_restores_both_schemas_and_reconciles(database, tmp_path, core):
    actor = str(uuid.uuid4())
    funded = account(core, actor)
    destination = account(core, actor)
    fund(core, database, actor, funded, "125.40")
    operation = invoke(core.Transfer, request(actor, funded, destination, "25.00"))
    invoke(
        core.CreateHold,
        pb.HoldRequest(
            context=context(actor),
            account_id=funded,
            amount=pb.Money(amount="10.00", currency="DB"),
            idempotency_key=uuid.uuid4().hex,
            request_fingerprint="b" * 64,
            reason="restore test reservation",
        ),
    )
    source = os.environ["TEST_DATABASE_URL"]
    archive = tmp_path / "horizon-test.dump"
    opts = conninfo_to_dict(source)
    env = {
        **os.environ,
        "PGHOST": opts["host"],
        "PGPORT": opts.get("port", "5432"),
        "PGUSER": opts.get("user", "postgres"),
        "PGDATABASE": opts["dbname"],
    }
    if "password" in opts:
        env["PGPASSWORD"] = opts["password"]
    subprocess.run(
        [executable("pg_dump"), "--format=custom", "--file", str(archive)],
        env=env,
        check=True,
        capture_output=True,
    )
    assert archive.stat().st_size > 0
    subprocess.run(
        [executable("pg_restore"), "--list", str(archive)],
        check=True,
        capture_output=True,
    )
    target_name = "horizon_test_restore_" + uuid.uuid4().hex[:12]
    maintenance = {**opts, "dbname": "postgres"}
    with psycopg.connect(make_conninfo(**maintenance), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_name)))
    restore_env = {
        **os.environ,
        "PGHOST": opts["host"],
        "PGPORT": opts.get("port", "5432"),
        "PGUSER": opts.get("user", "postgres"),
    }
    if "password" in opts:
        restore_env["PGPASSWORD"] = opts["password"]
    subprocess.run(
        [
            executable("pg_restore"),
            "--exit-on-error",
            "--dbname",
            target_name,
            str(archive),
        ],
        env=restore_env,
        check=True,
        capture_output=True,
    )
    with psycopg.connect(make_conninfo(**{**opts, "dbname": target_name})) as restored:
        assert restored.execute(
            "SELECT balance::text,held::text FROM finance.accounts WHERE id=%s", (funded,)
        ).fetchone() == ("100.40", "10.00")
        assert (
            restored.execute(
                "SELECT amount::text FROM finance.financial_operations WHERE id=%s", (operation.id,)
            ).fetchone()[0]
            == "25.00"
        )
        assert (
            restored.execute("SELECT version_num FROM finance.alembic_version").fetchone()
            == database.execute("SELECT version_num FROM finance.alembic_version").fetchone()
        )
        assert (
            restored.execute("SELECT version_num FROM auth.alembic_version").fetchone()
            == database.execute("SELECT version_num FROM auth.alembic_version").fetchone()
        )
        assert (
            restored.execute("SELECT COUNT(*) FROM finance.financial_operations").fetchone()
            == database.execute("SELECT COUNT(*) FROM finance.financial_operations").fetchone()
        )
        assert (
            restored.execute("SELECT COUNT(*) FROM auth.application_users").fetchone()
            == database.execute("SELECT COUNT(*) FROM auth.application_users").fetchone()
        )
        assert (
            restored.execute(
                "SELECT COUNT(*) FROM finance.reconciliation WHERE balance<>ledger_balance OR held<>active_holds"
            ).fetchone()[0]
            == 0
        )

"""Upgrade real 2.2 data to 2.3 without rewriting balances, ledger or old keys."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from bootstrap import bootstrap


def test_upgrade_22_to_23_preserves_existing_records():
    if not os.environ.get("TEST_ADMIN_DATABASE_URL"):
        pytest.skip("isolated database bootstrap required")
    config = bootstrap("0005")
    actor, account, key = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(config["TEST_DATABASE_URL"]) as db:
        db.execute(
            "INSERT INTO auth.application_users(id,display_name) VALUES(%s,'Legacy 2.2')",
            (actor,),
        )
        db.execute(
            "INSERT INTO auth.api_keys(id,owner_id,prefix,secret_hash,name,scopes,account_ids) VALUES(%s,%s,%s,%s,'Legacy key',ARRAY['accounts.read'],ARRAY[%s]::uuid[])",
            (key, actor, uuid.uuid4().hex[:12], "a" * 64, account),
        )
        db.execute(
            "INSERT INTO finance.accounts(id,account_type,owner_principal_id) VALUES(%s,'personal',%s)",
            (account, actor),
        )
        db.execute(
            "INSERT INTO finance.account_permissions(account_id,principal_id,permission,granted_by) VALUES(%s,%s,'account.manage',%s)",
            (account, actor, actor),
        )
        db.commit()
        tables = [
            "finance.accounts",
            "finance.financial_operations",
            "finance.ledger_transactions",
            "finance.ledger_entries",
            "finance.authorities",
            "auth.application_users",
            "auth.admin_roles",
            "auth.role_permissions",
            "auth.discord_outbox",
            "auth.terms_versions",
        ]
        before = {
            t: db.execute(
                "SELECT row_to_json(r)::text FROM " + t + " r ORDER BY 1"
            ).fetchall()
            for t in tables
        }
        previous_key = db.execute(
            "SELECT (to_jsonb(k)-'organization_id')::text FROM auth.api_keys k WHERE id=%s",
            (key,),
        ).fetchone()[0]
        db.commit()
        root = Path(__file__).resolve().parents[2]
        for folder, var, ini in (
            ("database/finance", "FINANCE_DATABASE_URL", "alembic.ini"),
            ("backend", "AUTH_DATABASE_URL", "alembic-auth.ini"),
        ):
            subprocess.run(
                [sys.executable, "-m", "alembic", "-c", ini, "upgrade", "head"],
                cwd=root / folder,
                env={**os.environ, var: config["TEST_" + var]},
                check=True,
            )
        for table in tables:
            assert (
                db.execute(
                    "SELECT row_to_json(r)::text FROM "
                    + table
                    + (
                        " r WHERE permission NOT LIKE 'admin.exchange.%'"
                        if table == "auth.role_permissions"
                        else " r"
                    )
                    + " ORDER BY 1"
                ).fetchall()
                == before[table]
            )
        assert (
            db.execute(
                "SELECT (to_jsonb(k)-'organization_id')::text FROM auth.api_keys k WHERE id=%s",
                (key,),
            ).fetchone()[0]
            == previous_key
        )
        assert (
            db.execute(
                "SELECT count(*) FROM finance.account_permissions WHERE account_id=%s AND permission LIKE 'payment_requests.%%'",
                (account,),
            ).fetchone()[0]
            == 3
        )
        assert (
            db.execute("SELECT count(*) FROM finance.payment_requests").fetchone()[0]
            == 0
        )

"""An existing 2.3 organization upgrades without new rights for ordinary members."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from bootstrap import bootstrap


def test_upgrade_23_to_24_preserves_accounts_and_limits_permission_backfill():
    if not os.environ.get("TEST_ADMIN_DATABASE_URL"):
        pytest.skip("isolated database bootstrap required")
    config = bootstrap("0006")
    owner, member, org, account = (uuid.uuid4() for _ in range(4))
    with psycopg.connect(config["TEST_DATABASE_URL"]) as db:
        db.execute(
            "INSERT INTO finance.organizations(id,owner_id,name) VALUES(%s,%s,'Legacy employer')",
            (org, owner),
        )
        db.execute(
            "INSERT INTO finance.organization_members(organization_id,principal_id,role,permissions) VALUES(%s,%s,'owner',ARRAY['accounts.read']),(%s,%s,'member',ARRAY['history.read'])",
            (org, owner, org, member),
        )
        db.execute(
            "INSERT INTO finance.accounts(id,account_type,organization_id,name) VALUES(%s,'organization',%s,'Legacy account')",
            (account, org),
        )
        db.execute(
            "INSERT INTO finance.account_permissions(account_id,principal_id,permission,granted_by) VALUES(%s,%s,'accounts.read',%s),(%s,%s,'history.read',%s)",
            (account, owner, owner, account, member, owner),
        )
        db.commit()
        tables = [
            "finance.accounts",
            "finance.financial_operations",
            "finance.ledger_transactions",
            "finance.ledger_entries",
            "auth.api_keys",
            "auth.application_users",
        ]
        before = {
            t: db.execute(
                "SELECT row_to_json(r)::text FROM " + t + " r ORDER BY 1"
            ).fetchall()
            for t in tables
        }
        db.commit()
        root = Path(__file__).resolve().parents[2]
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            cwd=root / "database/finance",
            env={
                **os.environ,
                "FINANCE_DATABASE_URL": config["TEST_FINANCE_DATABASE_URL"],
            },
            check=True,
        )
        for table in tables:
            assert (
                db.execute(
                    "SELECT row_to_json(r)::text FROM " + table + " r ORDER BY 1"
                ).fetchall()
                == before[table]
            )
        assert db.execute(
            "SELECT permissions FROM finance.organization_members WHERE organization_id=%s AND principal_id=%s",
            (org, member),
        ).fetchone()[0] == ["history.read"]
        assert (
            db.execute(
                "SELECT count(*) FROM finance.account_permissions WHERE account_id=%s AND principal_id=%s",
                (account, member),
            ).fetchone()[0]
            == 1
        )
        rights = db.execute(
            "SELECT permissions FROM finance.organization_members WHERE organization_id=%s AND principal_id=%s",
            (org, owner),
        ).fetchone()[0]
        assert (
            len(rights) == 14
            and "payroll.execute" in rights
            and "analytics.read" in rights
        )
        assert (
            db.execute(
                "SELECT count(*) FROM finance.business_categories WHERE organization_id=%s",
                (org,),
            ).fetchone()[0]
            == 7
        )
        assert (
            db.execute("SELECT count(*) FROM finance.payroll_schedules").fetchone()[0]
            == 0
        )
        assert (
            db.execute("SELECT version_num FROM auth.alembic_version").fetchone()[0]
            == "auth_0006"
        )

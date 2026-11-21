"""Real 2.1 -> 2.2 migration preserves the existing grant/security/financial rows."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from bootstrap import bootstrap


def test_upgrade_21_to_22_preserves_direct_permissions_and_all_existing_tables():
    if not os.environ.get("TEST_ADMIN_DATABASE_URL"):
        pytest.skip("isolated database bootstrap required")
    config = bootstrap("0004")
    user = uuid.uuid4()
    account = uuid.uuid4()
    org = uuid.uuid4()
    with psycopg.connect(config["TEST_DATABASE_URL"]) as db:
        db.execute(
            "INSERT INTO auth.application_users(id,display_name,bank_username,banking_ready) VALUES(%s,'Legacy','LegacyUser',true)",
            (user,),
        )
        db.execute(
            "INSERT INTO auth.user_permissions(user_id,permission,granted_by,reason) VALUES(%s,'admin.issuance',%s,'Existing mandate')",
            (user, user),
        )
        db.execute(
            "INSERT INTO finance.accounts(id,account_type,owner_principal_id) VALUES(%s,'personal',%s)",
            (account, user),
        )
        db.execute(
            "INSERT INTO finance.account_permissions(account_id,principal_id,permission,granted_by) VALUES(%s,%s,'issue.create',%s)",
            (account, user, user),
        )
        db.execute(
            "INSERT INTO finance.authorities(principal_id,permission,granted_by,reason) VALUES(%s,'issue.create',%s,'Legacy mandate')",
            (user, user),
        )
        db.execute(
            "INSERT INTO auth.organizations(id,name,owner_id) VALUES(%s,'Legacy org',%s)",
            (org, user),
        )
        db.execute(
            "INSERT INTO auth.organization_members(id,organization_id,user_id,role) VALUES(%s,%s,%s,'owner')",
            (uuid.uuid4(), org, user),
        )
        db.execute(
            "INSERT INTO auth.notification_preferences(user_id,discord_enabled) VALUES(%s,false)",
            (user,),
        )
        db.execute(
            "INSERT INTO auth.user_notifications(id,user_id,event_key,kind,title,body) VALUES(%s,%s,'old','security','Old','Retained')",
            (uuid.uuid4(), user),
        )
        db.commit()
        tables = [
            "auth.application_users",
            "auth.user_permissions",
            "auth.organizations",
            "auth.organization_members",
            "auth.user_notifications",
            "finance.accounts",
            "finance.account_permissions",
            "finance.authorities",
            "finance.financial_operations",
            "finance.ledger_transactions",
            "finance.ledger_entries",
            "finance.holds",
            "auth.api_keys",
            "auth.user_sessions",
        ]
        before = {
            t: db.execute(
                "SELECT row_to_json(r)::text FROM "
                + t
                + " r ORDER BY row_to_json(r)::text"
            ).fetchall()
            for t in tables
        }
    root = Path(__file__).resolve().parents[2]
    for folder, variable, ini in [
        ("backend", "AUTH_DATABASE_URL", "alembic-auth.ini"),
        ("database/finance", "FINANCE_DATABASE_URL", "alembic.ini"),
    ]:
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", ini, "upgrade", "head"],
            cwd=root / folder,
            env={**os.environ, variable: config["TEST_" + variable]},
            check=True,
        )
    with psycopg.connect(config["TEST_DATABASE_URL"]) as db:
        for t in tables:
            assert (
                db.execute(
                    "SELECT row_to_json(r)::text FROM "
                    + t
                    + (
                        " r WHERE permission<>'exchange.trade'"
                        if t == "finance.account_permissions"
                        else " r"
                    )
                    + " ORDER BY row_to_json(r)::text"
                ).fetchall()
                == before[t]
            ), t
        assert db.execute(
            "SELECT permission FROM auth.effective_permissions WHERE user_id=%s",
            (user,),
        ).fetchone() == ("admin.issuance",)
        assert db.execute(
            "SELECT discord_enabled,incoming_enabled,outgoing_enabled FROM auth.notification_preferences WHERE user_id=%s",
            (user,),
        ).fetchone() == (False, True, True)
        assert (
            db.execute("SELECT count(*) FROM finance.event_outbox").fetchone()[0] == 0
        )
        assert db.execute("SELECT count(*) FROM auth.user_roles").fetchone()[0] == 0
    env = {
        **os.environ,
        "AUTH_DATABASE_URL": config["TEST_AUTH_DATABASE_URL"].replace(
            "postgresql://", "postgresql+asyncpg://"
        ),
        "PYTHONPATH": str(root / "backend"),
        "ENVIRONMENT": "test",
    }
    command = [sys.executable, "-m", "app.bootstrap_admin", "--user", "LegacyUser"]
    first = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert str(user) in first.stdout
    repeated = subprocess.run(
        command, cwd=root, env=env, capture_output=True, text=True
    )
    assert repeated.returncode != 0 and "already exists" in repeated.stderr
    with psycopg.connect(config["TEST_DATABASE_URL"]) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM auth.audit_events WHERE action='role.bootstrap'"
            ).fetchone()[0]
            == 1
        )
        assert db.execute(
            "SELECT permission FROM finance.authorities WHERE principal_id=%s", (user,)
        ).fetchall() == [("issue.create",)]

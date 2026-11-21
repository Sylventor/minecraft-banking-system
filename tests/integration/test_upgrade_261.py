"""Upgrade the real auth_0007 schema; never rewrite financial evidence or existing identity IDs."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from bootstrap import bootstrap


def test_upgrade_26_to_261_preserves_all_existing_users_sessions_and_financial_tables():
    if not os.environ.get("TEST_ADMIN_DATABASE_URL"):
        pytest.skip("isolated database required")
    cfg = bootstrap("0007")
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=root / "database/finance",
        env={**os.environ, "FINANCE_DATABASE_URL": cfg["TEST_FINANCE_DATABASE_URL"]},
        check=True,
    )
    user, account = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(cfg["TEST_DATABASE_URL"]) as db:
        db.execute(
            "INSERT INTO auth.application_users(id,display_name,bank_username) VALUES(%s,'Legacy Discord user','legacy_before_261')",
            (user,),
        )
        db.execute(
            "INSERT INTO auth.external_identities(id,user_id,provider,external_id,username) VALUES(%s,%s,'discord',%s,'Legacy Discord')",
            (uuid.uuid4(), user, str(uuid.uuid4().int)),
        )
        db.execute(
            "INSERT INTO auth.user_sessions(token_hash,user_id,expires_at) VALUES(%s,%s,now()+interval '1 day')",
            ("d" * 64, user),
        )
        db.execute(
            "INSERT INTO finance.accounts(id,account_type,owner_principal_id) VALUES(%s,'personal',%s)",
            (account, user),
        )
        db.commit()
        tables = [
            "finance." + row[0]
            for row in db.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='finance' ORDER BY tablename"
            )
        ]
        tables += [
            "auth.application_users",
            "auth.external_identities",
            "auth.api_keys",
            "auth.organization_members",
            "auth.user_roles",
            "auth.audit_events",
        ]
        before = {
            table: db.execute(
                "SELECT row_to_json(r)::text FROM " + table + " r ORDER BY 1"
            ).fetchall()
            for table in tables
        }
        session = db.execute(
            "SELECT token_hash,user_id,created_at,expires_at,revoked_at,absolute_expires_at FROM auth.user_sessions"
        ).fetchall()
        db.commit()
        subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "alembic-auth.ini",
                "upgrade",
                "head",
            ],
            cwd=root / "backend",
            env={**os.environ, "AUTH_DATABASE_URL": cfg["TEST_AUTH_DATABASE_URL"]},
            check=True,
        )
        for table in tables:
            assert (
                db.execute(
                    "SELECT row_to_json(r)::text FROM " + table + " r ORDER BY 1"
                ).fetchall()
                == before[table]
            )
        assert (
            db.execute(
                "SELECT token_hash,user_id,created_at,expires_at,revoked_at,absolute_expires_at FROM auth.user_sessions"
            ).fetchall()
            == session
        )
        assert (
            db.execute("SELECT auth_method FROM auth.user_sessions").fetchone()[0]
            == "discord"
        )
        assert (
            db.execute("SELECT count(*) FROM auth.password_credentials").fetchone()[0]
            == 0
        )
        assert (
            db.execute("SELECT version_num FROM auth.alembic_version").fetchone()[0]
            == "auth_0009"
        )
        assert (
            db.execute("SELECT version_num FROM finance.alembic_version").fetchone()[0]
            == "finance_0008"
        )

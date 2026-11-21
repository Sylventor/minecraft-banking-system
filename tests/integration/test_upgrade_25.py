"""Upgrade real 2.4 session rows without invalidating token hashes or legacy records."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from bootstrap import bootstrap


def test_upgrade_24_to_25_preserves_cookie_hashes_and_existing_accounts():
    if not os.environ.get("TEST_ADMIN_DATABASE_URL"):
        pytest.skip("isolated database required")
    cfg = bootstrap("0006")
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "finance_0007",
        ],
        cwd=root / "database/finance",
        env={**os.environ, "FINANCE_DATABASE_URL": cfg["TEST_FINANCE_DATABASE_URL"]},
        check=True,
    )
    user, account = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(cfg["TEST_DATABASE_URL"]) as db:
        db.execute(
            "INSERT INTO auth.application_users(id,display_name) VALUES(%s,'Legacy 2.4')",
            (user,),
        )
        db.execute(
            "INSERT INTO auth.user_sessions(token_hash,user_id,expires_at) VALUES(%s,%s,now()+interval '8 hours')",
            ("c" * 64, user),
        )
        db.execute(
            "INSERT INTO finance.accounts(id,account_type,owner_principal_id) VALUES(%s,'personal',%s)",
            (account, user),
        )
        db.commit()
        old = db.execute(
            "SELECT token_hash,user_id,created_at,expires_at,revoked_at FROM auth.user_sessions"
        ).fetchall()
        tables = [
            "finance.accounts",
            "finance.financial_operations",
            "finance.ledger_entries",
            "finance.payroll_batches",
            "finance.payment_requests",
            "auth.api_keys",
        ]
        before = {
            t: db.execute(
                "SELECT row_to_json(r)::text FROM " + t + " r ORDER BY 1"
            ).fetchall()
            for t in tables
        }
        db.commit()
        for folder, var, ini in (
            ("database/finance", "FINANCE_DATABASE_URL", "alembic.ini"),
            ("backend", "AUTH_DATABASE_URL", "alembic-auth.ini"),
        ):
            subprocess.run(
                [sys.executable, "-m", "alembic", "-c", ini, "upgrade", "head"],
                cwd=root / folder,
                env={**os.environ, var: cfg["TEST_" + var]},
                check=True,
            )
        assert (
            db.execute(
                "SELECT token_hash,user_id,created_at,expires_at,revoked_at FROM auth.user_sessions"
            ).fetchall()
            == old
        )
        for t in tables:
            assert (
                db.execute(
                    "SELECT row_to_json(r)::text FROM " + t + " r ORDER BY 1"
                ).fetchall()
                == before[t]
            )
        assert db.execute(
            "SELECT absolute_expires_at=created_at+interval '90 days' FROM auth.user_sessions"
        ).fetchone()[0]
        assert (
            db.execute("SELECT count(*) FROM finance.exchange_trades").fetchone()[0]
            == 0
        )

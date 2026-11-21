"""Exercise the real 0003 -> 0004 upgrade with funded legacy data and live constraints."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from bootstrap import bootstrap


def test_additive_upgrade_preserves_ledger_permissions_keys_sessions_and_identities():
    if not os.environ.get("TEST_ADMIN_DATABASE_URL"):
        pytest.skip(
            "maintenance connection required to create an isolated legacy database"
        )
    config = bootstrap("0003")
    owner = "11111111-1111-4111-8111-000000000001"
    other = "11111111-1111-4111-8111-000000000002"
    account, issuer, operation, transaction, holdop, hold = [
        uuid.uuid4() for _ in range(6)
    ]
    with psycopg.connect(config["TEST_DATABASE_URL"]) as db:
        issuer = db.execute(
            "SELECT id FROM finance.accounts WHERE account_type='system'"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO auth.application_users(id,display_name) VALUES(%s,'Старый клиент'),(%s,'Другой клиент')",
            (owner, other),
        )
        db.execute(
            "INSERT INTO auth.external_identities(id,user_id,provider,external_id,username) VALUES(%s,%s,'discord','123456789123456789','legacy')",
            (uuid.uuid4(), owner),
        )
        db.execute(
            "INSERT INTO auth.api_keys(id,owner_id,prefix,secret_hash,name,scopes) VALUES(%s,%s,'legacy-key',%s,'Legacy',ARRAY['balance.read'])",
            (uuid.uuid4(), owner, "a" * 64),
        )
        db.execute(
            "INSERT INTO auth.user_sessions(token_hash,user_id,expires_at) VALUES(%s,%s,now()+interval '1 day')",
            ("b" * 64, owner),
        )
        db.execute(
            "INSERT INTO finance.accounts(id,account_type,balance,held) VALUES(%s,'personal',125.40,10.00)",
            (account,),
        )
        db.execute("UPDATE finance.accounts SET balance=-125.40 WHERE id=%s", (issuer,))
        db.execute(
            "INSERT INTO finance.account_permissions(account_id,principal_id,permission,granted_by) VALUES(%s,%s,'account.manage',%s)",
            (account, owner, owner),
        )
        db.execute(
            "INSERT INTO finance.financial_operations(id,operation_type,status,source_account_id,destination_account_id,amount,currency,initiator_id,request_id,reason) VALUES(%s,'issue','completed',%s,%s,125.40,'dB',%s,'legacy-issue','Legacy issuance')",
            (operation, issuer, account, owner),
        )
        db.execute(
            "INSERT INTO finance.ledger_transactions(id,operation_id) VALUES(%s,%s)",
            (transaction, operation),
        )
        db.execute(
            "INSERT INTO finance.ledger_entries(transaction_id,account_id,direction,amount,currency) VALUES(%s,%s,'debit',125.40,'dB'),(%s,%s,'credit',125.40,'dB')",
            (transaction, issuer, transaction, account),
        )
        db.execute(
            "INSERT INTO finance.issuance_records(operation_id,target_account_id,amount,authority_reference) VALUES(%s,%s,125.40,'legacy authority')",
            (operation, account),
        )
        db.execute(
            "INSERT INTO finance.financial_operations(id,operation_type,status,source_account_id,amount,currency,initiator_id,request_id) VALUES(%s,'hold','completed',%s,10.00,'dB',%s,'legacy-hold')",
            (holdop, account, owner),
        )
        db.execute(
            "INSERT INTO finance.holds(id,account_id,operation_id,amount,status) VALUES(%s,%s,%s,10.00,'active')",
            (hold, account, holdop),
        )
        db.execute(
            "INSERT INTO finance.idempotency_records(scope_id,operation_type,idempotency_key,request_fingerprint,state,operation_id) VALUES(%s,'issue','legacy-key-00000001',%s,'completed',%s)",
            (owner, "c" * 64, operation),
        )
        db.commit()  # Constraints are enabled; an inconsistent fixture cannot reach migration.
        tables = [
            "finance.financial_operations",
            "finance.ledger_transactions",
            "finance.ledger_entries",
            "finance.holds",
            "finance.issuance_records",
            "finance.idempotency_records",
            "finance.account_permissions",
            "auth.api_keys",
            "auth.user_sessions",
        ]
        before = {
            table: db.execute(
                "SELECT (to_jsonb(t)-ARRAY['organization_id','public_id','last_activity_at','absolute_expires_at','client_info','auth_method'])::text FROM "
                + table
                + " t ORDER BY 1"
            ).fetchall()
            for table in tables
        }
    root = Path(__file__).resolve().parents[2]
    for folder, variable, ini in [
        ("database/finance", "FINANCE_DATABASE_URL", "alembic.ini"),
        ("backend", "AUTH_DATABASE_URL", "alembic-auth.ini"),
    ]:
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", ini, "upgrade", "head"],
            cwd=root / folder,
            env={**os.environ, variable: config["TEST_" + variable]},
            check=True,
        )
    with psycopg.connect(config["TEST_DATABASE_URL"]) as db:
        for table in tables:
            assert (
                db.execute(
                    "SELECT (to_jsonb(t)-ARRAY['organization_id','public_id','last_activity_at','absolute_expires_at','client_info','auth_method'])::text FROM "
                    + table
                    + (
                        " t WHERE permission NOT IN ('payment_requests.create','payment_requests.read','payment_requests.cancel','exchange.trade')"
                        if table == "finance.account_permissions"
                        else " t"
                    )
                    + " ORDER BY 1"
                ).fetchall()
                == before[table]
            ), table
        assert db.execute("SELECT auth_method FROM auth.user_sessions").fetchone()[0] == "discord"
        assert db.execute(
            "SELECT balance::text,held::text,owner_principal_id::text FROM finance.accounts WHERE id=%s",
            (account,),
        ).fetchone() == ("125.40", "10.00", owner)
        assert (
            db.execute(
                "SELECT count(*) FROM finance.reconciliation WHERE balance<>ledger_balance OR held<>active_holds"
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT count(DISTINCT bank_username) FROM auth.application_users"
            ).fetchone()[0]
            == 2
        )
        assert (
            db.execute(
                "SELECT display_name FROM auth.application_users WHERE id=%s", (owner,)
            ).fetchone()[0]
            == "Старый клиент"
        )
        assert (
            db.execute(
                "SELECT username FROM auth.external_identities WHERE user_id=%s",
                (owner,),
            ).fetchone()[0]
            == "legacy"
        )

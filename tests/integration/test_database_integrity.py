from __future__ import annotations

import uuid

import psycopg
import pytest


def test_migration_streams_are_independent(database):
    assert (
        database.execute("SELECT version_num FROM finance.alembic_version").fetchone()[
            0
        ]
        == "finance_0012"
    )
    assert (
        database.execute("SELECT version_num FROM auth.alembic_version").fetchone()[0]
        == "auth_0013"
    )


def test_auth_role_cannot_read_or_modify_financial_schema(database):
    database.execute("SET LOCAL ROLE horizon_auth")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        database.execute("SELECT * FROM finance.accounts")
    database.rollback()


def test_core_role_cannot_read_authentication_secrets(database):
    database.execute("SET LOCAL ROLE horizon_finance")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        database.execute("SELECT secret_hash FROM auth.api_keys")
    database.rollback()


def test_direct_balance_change_without_ledger_is_rejected_at_commit(database):
    account = uuid.uuid4()
    database.execute(
        "INSERT INTO finance.accounts(id,account_type) VALUES(%s,'personal')",
        (account,),
    )
    database.commit()
    database.execute("UPDATE finance.accounts SET balance=1.00 WHERE id=%s", (account,))
    with pytest.raises(psycopg.errors.CheckViolation):
        database.commit()
    database.rollback()
    assert (
        str(
            database.execute(
                "SELECT balance FROM finance.accounts WHERE id=%s", (account,)
            ).fetchone()[0]
        )
        == "0.00"
    )


def test_audit_cannot_be_edited_or_removed(database):
    ident = uuid.uuid4()
    database.execute(
        "INSERT INTO auth.audit_events(id,action,target_type,target_id) VALUES(%s,'test','test','test')",
        (ident,),
    )
    database.commit()
    with pytest.raises(psycopg.errors.CheckViolation):
        database.execute("DELETE FROM auth.audit_events WHERE id=%s", (ident,))
    database.rollback()


def test_unbalanced_ledger_cannot_commit(database):
    account = uuid.uuid4()
    operation = uuid.uuid4()
    ledger = uuid.uuid4()
    database.execute(
        "INSERT INTO finance.accounts(id,account_type) VALUES(%s,'personal')",
        (account,),
    )
    database.commit()
    database.execute(
        "INSERT INTO finance.financial_operations(id,operation_type,status,destination_account_id,amount,currency,initiator_id,request_id) VALUES(%s,'issue','completed',%s,1.00,'dB',%s,'test')",
        (operation, account, uuid.uuid4()),
    )
    database.execute(
        "INSERT INTO finance.ledger_transactions(id,operation_id) VALUES(%s,%s)",
        (ledger, operation),
    )
    database.execute(
        "INSERT INTO finance.ledger_entries(transaction_id,account_id,direction,amount,currency) VALUES(%s,%s,'credit',1.00,'dB')",
        (ledger, account),
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        database.commit()
    database.rollback()


def test_completed_operation_cannot_exist_without_ledger(database):
    source = uuid.uuid4()
    target = uuid.uuid4()
    operation = uuid.uuid4()
    database.execute(
        "INSERT INTO finance.accounts(id,account_type) VALUES(%s,'personal'),(%s,'personal')",
        (source, target),
    )
    database.commit()
    database.execute(
        "INSERT INTO finance.financial_operations(id,operation_type,status,source_account_id,destination_account_id,amount,currency,initiator_id,request_id) VALUES(%s,'transfer','completed',%s,%s,1.00,'dB',%s,'test')",
        (operation, source, target, uuid.uuid4()),
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        database.commit()
    database.rollback()
    assert (
        database.execute(
            "SELECT COUNT(*) FROM finance.financial_operations WHERE id=%s",
            (operation,),
        ).fetchone()[0]
        == 0
    )

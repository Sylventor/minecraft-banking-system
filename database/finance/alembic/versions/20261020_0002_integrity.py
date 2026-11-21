"""Financial authority, limits, and deferred ledger/cache reconciliation."""
from alembic import op

revision = "finance_0002"
down_revision = "finance_0001"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""
    CREATE TABLE finance.authorities (
      principal_id uuid NOT NULL, permission text NOT NULL CHECK(permission IN ('issue.create','refund.create')),
      granted_by uuid NOT NULL, reason text NOT NULL CHECK(length(reason)>0),
      created_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz,
      PRIMARY KEY(principal_id,permission)
    );
    CREATE TABLE finance.policy (
      id integer PRIMARY KEY CHECK(id=1), max_operation numeric(20,2) NOT NULL CHECK(max_operation>0),
      max_issuance numeric(20,2) NOT NULL CHECK(max_issuance>0),
      updated_at timestamptz NOT NULL DEFAULT now()
    );
    INSERT INTO finance.policy VALUES(1,9999999999999999.99,9999999999999999.99,now());
    ALTER TABLE finance.accounts ADD CONSTRAINT supported_balance_range
      CHECK(balance BETWEEN -9999999999999999.99 AND 9999999999999999.99);
    CREATE UNIQUE INDEX one_issuance_account ON finance.accounts(account_type) WHERE account_type='system';
    INSERT INTO finance.accounts(id,account_type) VALUES('00000000-0000-0000-0000-000000000001','system');

    CREATE FUNCTION finance.check_ledger_balance() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE tid uuid; delta numeric; count_entries integer;
    BEGIN
      IF TG_TABLE_NAME='ledger_entries' THEN tid := NEW.transaction_id; ELSE tid := NEW.id; END IF;
      SELECT COALESCE(SUM(CASE direction WHEN 'credit' THEN amount ELSE -amount END),0),COUNT(*)
        INTO delta,count_entries FROM finance.ledger_entries WHERE transaction_id=tid;
      IF delta<>0 OR count_entries<2 THEN RAISE EXCEPTION 'unbalanced ledger transaction' USING ERRCODE='23514'; END IF;
      IF EXISTS(SELECT 1 FROM finance.accounts a WHERE a.id IN
          (SELECT account_id FROM finance.ledger_entries WHERE transaction_id=tid)
          AND a.balance<>(SELECT COALESCE(SUM(CASE e.direction WHEN 'credit' THEN e.amount ELSE -e.amount END),0)
                         FROM finance.ledger_entries e WHERE e.account_id=a.id)) THEN
        RAISE EXCEPTION 'ledger does not match account cache' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END; $$;
    CREATE CONSTRAINT TRIGGER ledger_transaction_balanced AFTER INSERT ON finance.ledger_transactions
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.check_ledger_balance();
    CREATE CONSTRAINT TRIGGER ledger_entry_balanced AFTER INSERT ON finance.ledger_entries
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.check_ledger_balance();

    CREATE FUNCTION finance.check_account_cache() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE expected numeric; reserved numeric; actual numeric; actual_held numeric;
    BEGIN
      SELECT balance,held INTO actual,actual_held FROM finance.accounts WHERE id=NEW.id;
      SELECT COALESCE(SUM(CASE direction WHEN 'credit' THEN amount ELSE -amount END),0)
        INTO expected FROM finance.ledger_entries WHERE account_id=NEW.id;
      SELECT COALESCE(SUM(amount),0) INTO reserved FROM finance.holds WHERE account_id=NEW.id AND status='active';
      IF actual<>expected OR actual_held<>reserved THEN
        RAISE EXCEPTION 'account cache does not match immutable ledger and active holds' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END; $$;
    CREATE CONSTRAINT TRIGGER account_cache_consistent AFTER INSERT OR UPDATE ON finance.accounts
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.check_account_cache();
    CREATE VIEW finance.reconciliation AS
      SELECT a.id,a.balance,a.held,
        COALESCE((SELECT SUM(CASE e.direction WHEN 'credit' THEN e.amount ELSE -e.amount END)
          FROM finance.ledger_entries e WHERE e.account_id=a.id),0) AS ledger_balance,
        COALESCE((SELECT SUM(h.amount) FROM finance.holds h WHERE h.account_id=a.id AND h.status='active'),0) AS active_holds
      FROM finance.accounts a;
    """)

def downgrade() -> None:
    op.execute("""
    DROP VIEW finance.reconciliation;
    DROP TRIGGER account_cache_consistent ON finance.accounts;
    DROP FUNCTION finance.check_account_cache();
    DROP TRIGGER ledger_entry_balanced ON finance.ledger_entries;
    DROP TRIGGER ledger_transaction_balanced ON finance.ledger_transactions;
    DROP FUNCTION finance.check_ledger_balance();
    DROP INDEX finance.one_issuance_account;
    ALTER TABLE finance.accounts DROP CONSTRAINT supported_balance_range;
    DROP TABLE finance.policy;
    DROP TABLE finance.authorities;
    """)

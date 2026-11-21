"""Require operation evidence at commit and preserve the originating channel."""
from alembic import op

revision="finance_0003"
down_revision="finance_0002"
branch_labels=None
depends_on=None

def upgrade() -> None:
    op.execute("""
    ALTER TABLE finance.financial_operations ADD COLUMN source varchar(32) NOT NULL DEFAULT 'rest';
    ALTER TABLE finance.accounts ADD CONSTRAINT issuance_counter_only
      CHECK(account_type<>'system' OR (balance<=0 AND held=0));
    CREATE FUNCTION finance.check_operation_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE lid uuid; n integer; debits numeric; credits numeric;
    BEGIN
      IF NEW.status<>'completed' THEN RETURN NULL; END IF;
      IF NEW.operation_type='hold' THEN
        IF NOT EXISTS(SELECT 1 FROM finance.holds WHERE operation_id=NEW.id AND account_id=NEW.source_account_id AND amount=NEW.amount) THEN
          RAISE EXCEPTION 'hold evidence missing' USING ERRCODE='23514';
        END IF;
      ELSIF NEW.operation_type='release_hold' THEN
        IF NOT EXISTS(SELECT 1 FROM finance.holds WHERE operation_id=NEW.related_operation_id AND account_id=NEW.source_account_id AND amount=NEW.amount AND status='released') THEN
          RAISE EXCEPTION 'release evidence missing' USING ERRCODE='23514';
        END IF;
      ELSE
        SELECT id INTO lid FROM finance.ledger_transactions WHERE operation_id=NEW.id;
        IF lid IS NULL THEN RAISE EXCEPTION 'ledger evidence missing' USING ERRCODE='23514'; END IF;
        SELECT COUNT(*),COALESCE(SUM(amount) FILTER(WHERE direction='debit' AND account_id=NEW.source_account_id),0),
          COALESCE(SUM(amount) FILTER(WHERE direction='credit' AND account_id=NEW.destination_account_id),0)
          INTO n,debits,credits FROM finance.ledger_entries WHERE transaction_id=lid;
        IF n<>2 OR debits<>NEW.amount OR credits<>NEW.amount THEN
          RAISE EXCEPTION 'operation does not match ledger evidence' USING ERRCODE='23514';
        END IF;
        IF NEW.operation_type='issue' AND NOT EXISTS(SELECT 1 FROM finance.issuance_records WHERE operation_id=NEW.id AND amount=NEW.amount AND target_account_id=NEW.destination_account_id) THEN
          RAISE EXCEPTION 'issuance authority evidence missing' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NULL;
    END; $$;
    CREATE CONSTRAINT TRIGGER operation_has_evidence AFTER INSERT ON finance.financial_operations
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.check_operation_evidence();
    """)

def downgrade() -> None:
    op.execute("""
    DROP TRIGGER operation_has_evidence ON finance.financial_operations;
    DROP FUNCTION finance.check_operation_evidence();
    ALTER TABLE finance.accounts DROP CONSTRAINT issuance_counter_only;
    ALTER TABLE finance.financial_operations DROP COLUMN source;
    """)

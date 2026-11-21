"""Allow organization owners to approve their own payroll and dividends."""

from alembic import op

revision = "finance_0010"
down_revision = "finance_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    DO $$ DECLARE c record; BEGIN
      FOR c IN SELECT conrelid::regclass AS tbl, conname FROM pg_constraint
        WHERE contype='c' AND conrelid IN ('finance.payroll_batches'::regclass,
          'finance.payroll_schedules'::regclass,'finance.exchange_dividends'::regclass)
        AND pg_get_constraintdef(oid) ~ 'approved_by[[:space:]]*<>[[:space:]]*created_by'
      LOOP EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',c.tbl,c.conname); END LOOP;
    END $$;
    ALTER TABLE finance.payroll_schedules ADD CONSTRAINT payroll_schedule_active_approval
      CHECK(mode<>'automatic' OR status<>'active' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL));

    CREATE FUNCTION finance.check_owner_self_approval() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE org uuid;
    BEGIN
      IF NEW.approved_by IS NULL OR NEW.approved_by<>NEW.created_by THEN RETURN NEW; END IF;
      IF TG_TABLE_NAME='exchange_dividends' THEN
        SELECT organization_id INTO org FROM finance.exchange_stocks WHERE id=NEW.stock_id;
      ELSE org:=NEW.organization_id;
      END IF;
      PERFORM id FROM finance.organizations WHERE id=org AND owner_id=NEW.approved_by FOR SHARE;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'self approval requires organization ownership' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER payroll_batch_owner_approval BEFORE INSERT OR UPDATE OF approved_by,created_by,organization_id
      ON finance.payroll_batches FOR EACH ROW EXECUTE FUNCTION finance.check_owner_self_approval();
    CREATE TRIGGER payroll_schedule_owner_approval BEFORE INSERT OR UPDATE OF approved_by,created_by,organization_id
      ON finance.payroll_schedules FOR EACH ROW EXECUTE FUNCTION finance.check_owner_self_approval();
    CREATE TRIGGER dividend_owner_approval BEFORE INSERT OR UPDATE OF approved_by,created_by,stock_id
      ON finance.exchange_dividends FOR EACH ROW EXECUTE FUNCTION finance.check_owner_self_approval();
    """)


def downgrade() -> None:
    # Restoring the old checks intentionally fails if self-approved owner records
    # exist: never erase their approval or rewrite financial history on downgrade.
    op.execute("""
    ALTER TABLE finance.payroll_batches ADD CONSTRAINT payroll_batch_separate_approval
      CHECK(approved_by IS NULL OR approved_by<>created_by);
    ALTER TABLE finance.exchange_dividends ADD CONSTRAINT dividend_separate_approval
      CHECK(approved_by IS NULL OR approved_by<>created_by);
    ALTER TABLE finance.payroll_schedules ADD CONSTRAINT payroll_schedule_separate_approval
      CHECK(mode<>'automatic' OR status<>'active' OR
        (approved_by IS NOT NULL AND approved_at IS NOT NULL AND approved_by<>created_by));
    DROP TRIGGER payroll_batch_owner_approval ON finance.payroll_batches;
    DROP TRIGGER payroll_schedule_owner_approval ON finance.payroll_schedules;
    DROP TRIGGER dividend_owner_approval ON finance.exchange_dividends;
    DROP FUNCTION finance.check_owner_self_approval();
    ALTER TABLE finance.payroll_schedules DROP CONSTRAINT payroll_schedule_active_approval;
    """)

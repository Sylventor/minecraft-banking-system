"""Initial core-owned immutable financial schema."""
from alembic import op

revision = "finance_0001"
down_revision = None
branch_labels = ("finance",)
depends_on = None

def upgrade() -> None:
    op.execute("""
    CREATE TABLE finance.accounts (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), account_type text NOT NULL CHECK (account_type IN ('personal','organization','system','technical')),
      status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','frozen','closed')), currency char(2) NOT NULL DEFAULT 'dB' CHECK (currency='dB'),
      balance numeric(20,2) NOT NULL DEFAULT 0.00 CHECK (scale(balance)=2), held numeric(20,2) NOT NULL DEFAULT 0.00 CHECK (scale(held)=2 AND held>=0),
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), CHECK (account_type='system' OR balance>=held)
    );
    CREATE TABLE finance.account_permissions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), account_id uuid NOT NULL REFERENCES finance.accounts(id) ON DELETE RESTRICT,
      principal_id uuid NOT NULL, permission text NOT NULL CHECK (permission IN ('accounts.read','balance.read','history.read','transfer','payment.create','hold.create','refund.create','issue.create','account.manage')),
      granted_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz,
      UNIQUE(account_id,principal_id,permission)
    );
    CREATE TABLE finance.financial_operations (
      id uuid PRIMARY KEY, operation_type text NOT NULL CHECK (operation_type IN ('transfer','payment','refund','hold','release_hold','capture_hold','issue')),
      status text NOT NULL CHECK (status IN ('completed','failed','reversed')), source_account_id uuid REFERENCES finance.accounts(id) ON DELETE RESTRICT,
      destination_account_id uuid REFERENCES finance.accounts(id) ON DELETE RESTRICT,
      amount numeric(20,2) NOT NULL CHECK (amount>0 AND scale(amount)=2), currency char(2) NOT NULL CHECK(currency='dB'),
      initiator_id uuid NOT NULL, request_id text NOT NULL, correlation_id text, reason text NOT NULL DEFAULT '', related_operation_id uuid REFERENCES finance.financial_operations(id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz, CHECK (source_account_id IS DISTINCT FROM destination_account_id)
    );
    CREATE TABLE finance.ledger_transactions (
      id uuid PRIMARY KEY, operation_id uuid NOT NULL UNIQUE REFERENCES finance.financial_operations(id) ON DELETE RESTRICT, created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE finance.ledger_entries (
      id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, transaction_id uuid NOT NULL REFERENCES finance.ledger_transactions(id) ON DELETE RESTRICT,
      account_id uuid NOT NULL REFERENCES finance.accounts(id) ON DELETE RESTRICT, direction text NOT NULL CHECK(direction IN ('debit','credit')),
      amount numeric(20,2) NOT NULL CHECK(amount>0 AND scale(amount)=2), currency char(2) NOT NULL CHECK(currency='dB'), created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE finance.holds (
      id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES finance.accounts(id) ON DELETE RESTRICT, operation_id uuid NOT NULL UNIQUE REFERENCES finance.financial_operations(id) ON DELETE RESTRICT,
      amount numeric(20,2) NOT NULL CHECK(amount>0 AND scale(amount)=2), status text NOT NULL CHECK(status IN ('active','released','captured')),
      created_at timestamptz NOT NULL DEFAULT now(), released_at timestamptz, captured_at timestamptz
    );
    CREATE TABLE finance.issuance_records (
      operation_id uuid PRIMARY KEY REFERENCES finance.financial_operations(id) ON DELETE RESTRICT, target_account_id uuid NOT NULL REFERENCES finance.accounts(id) ON DELETE RESTRICT,
      amount numeric(20,2) NOT NULL CHECK(amount>0 AND scale(amount)=2), authority_reference text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE finance.idempotency_records (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), scope_id uuid NOT NULL, operation_type text NOT NULL, idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 16 AND 255),
      request_fingerprint char(64) NOT NULL, state text NOT NULL CHECK(state IN ('processing','completed')), operation_id uuid REFERENCES finance.financial_operations(id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz, expires_at timestamptz NOT NULL DEFAULT now()+interval '90 days',
      UNIQUE(scope_id,operation_type,idempotency_key)
    );
    CREATE INDEX operations_account_created_idx ON finance.financial_operations(source_account_id,created_at DESC);
    CREATE INDEX operations_destination_created_idx ON finance.financial_operations(destination_account_id,created_at DESC);
    CREATE INDEX ledger_entries_account_idx ON finance.ledger_entries(account_id,created_at DESC);
    CREATE INDEX permissions_active_idx ON finance.account_permissions(account_id,principal_id) WHERE revoked_at IS NULL;
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION finance.prevent_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable financial history'; END; $$;
    CREATE TRIGGER financial_operations_immutable BEFORE UPDATE OR DELETE ON finance.financial_operations FOR EACH ROW EXECUTE FUNCTION finance.prevent_mutation();
    CREATE TRIGGER ledger_transactions_immutable BEFORE UPDATE OR DELETE ON finance.ledger_transactions FOR EACH ROW EXECUTE FUNCTION finance.prevent_mutation();
    CREATE TRIGGER ledger_entries_immutable BEFORE UPDATE OR DELETE ON finance.ledger_entries FOR EACH ROW EXECUTE FUNCTION finance.prevent_mutation();
    """)
    op.execute("REVOKE ALL ON SCHEMA finance FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA finance TO horizon_finance")
    op.execute("GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA finance TO horizon_finance")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA finance FROM horizon_auth")

def downgrade() -> None:
    op.execute("DROP SCHEMA finance CASCADE")

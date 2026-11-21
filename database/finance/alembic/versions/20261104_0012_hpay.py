"""HPay product catalogue and immutable purchase snapshots."""

from alembic import op

revision = "finance_0012"
down_revision = "finance_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE finance.hpay_products (
      id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      organization_id uuid NOT NULL REFERENCES finance.organizations(id),
      account_id uuid NOT NULL REFERENCES finance.accounts(id),
      name text NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 80),
      description text NOT NULL DEFAULT '' CHECK(length(description)<=500),
      price numeric(18,2) NOT NULL CHECK(price>0),
      status text NOT NULL CHECK(status IN ('active','disabled','archived')),
      activation_revision bigint NOT NULL DEFAULT 1,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE INDEX hpay_products_org ON finance.hpay_products(organization_id,id);
    CREATE TABLE finance.hpay_purchases (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      buyer_id uuid NOT NULL,
      product_id bigint NOT NULL REFERENCES finance.hpay_products(id),
      activation_revision bigint NOT NULL,
      organization_id uuid NOT NULL REFERENCES finance.organizations(id),
      account_id uuid NOT NULL REFERENCES finance.accounts(id),
      product_name text NOT NULL,
      merchant_name text NOT NULL,
      price numeric(18,2) NOT NULL CHECK(price>0),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '60 seconds',
      status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','cancelled','expired','failed')),
      completed_at timestamptz,
      protected_until timestamptz,
      operation_id uuid UNIQUE REFERENCES finance.financial_operations(id),
      CHECK((status='completed')=(operation_id IS NOT NULL AND completed_at IS NOT NULL AND protected_until IS NOT NULL))
    );
    CREATE UNIQUE INDEX hpay_one_pending ON finance.hpay_purchases(buyer_id) WHERE status='pending';
    CREATE INDEX hpay_recent ON finance.hpay_purchases(buyer_id,product_id,protected_until) WHERE status='completed';
    CREATE INDEX hpay_sales ON finance.hpay_purchases(organization_id,product_id,completed_at) WHERE status='completed';
    """)


def downgrade() -> None:
    raise RuntimeError("HPay purchase history must not be discarded")

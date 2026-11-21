"""Durable ATM fencing, physical evidence and core-owned settlement states."""

from alembic import op

revision = "finance_0009"
down_revision = "finance_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE finance.atm_runtime(
      id boolean PRIMARY KEY DEFAULT true CHECK(id),instance_id uuid NOT NULL,
      expires_at timestamptz NOT NULL,max_amount integer NOT NULL CHECK(max_amount BETWEEN 1 AND 2304),
      player_distance numeric NOT NULL CHECK(player_distance>0 AND player_distance<=32));
    CREATE TABLE finance.atm_devices(
      id varchar(32) PRIMARY KEY,username varchar(16) NOT NULL,is_master boolean NOT NULL,
      enabled boolean NOT NULL DEFAULT true,connected boolean NOT NULL DEFAULT false,
      instance_id uuid NOT NULL,dimension text NOT NULL DEFAULT '',position jsonb NOT NULL DEFAULT '{}',
      observations jsonb NOT NULL DEFAULT '{}',inventory integer NOT NULL DEFAULT 0 CHECK(inventory>=0),
      free_space integer NOT NULL DEFAULT 0 CHECK(free_space>=0),last_seen_at timestamptz NOT NULL DEFAULT now(),
      error varchar(500) NOT NULL DEFAULT '',container jsonb NOT NULL DEFAULT '{}');
    CREATE TABLE finance.atm_operations(
      id uuid PRIMARY KEY,request_key uuid NOT NULL UNIQUE,instance_id uuid NOT NULL,
      device_id varchar(32) NOT NULL REFERENCES finance.atm_devices(id),user_id uuid NOT NULL,
      player_uuid uuid NOT NULL,nickname varchar(16) NOT NULL,
      kind text NOT NULL CHECK(kind IN ('withdraw','deposit','collection')),
      quantity integer NOT NULL CHECK(quantity BETWEEN 1 AND 2304),
      account_id uuid REFERENCES finance.accounts(id),hold_id uuid REFERENCES finance.holds(id),
      operation_id uuid REFERENCES finance.financial_operations(id),
      status text NOT NULL CHECK(status IN ('CREATED','RESERVED','PROCESSING','COMPLETED','FAILED','REQUIRES_REVIEW')),
      evidence jsonb NOT NULL DEFAULT '{}',error varchar(500) NOT NULL DEFAULT '',
      created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now());
    CREATE UNIQUE INDEX atm_one_physical_operation ON finance.atm_operations(device_id)
      WHERE status NOT IN ('COMPLETED','FAILED');
    CREATE UNIQUE INDEX atm_one_player_operation ON finance.atm_operations(user_id)
      WHERE status NOT IN ('COMPLETED','FAILED');
    CREATE INDEX atm_operation_history ON finance.atm_operations(created_at DESC,id DESC);
    CREATE TABLE finance.atm_events(
      id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,device_id varchar(32),operation_id uuid,
      actor_id uuid NOT NULL,action text NOT NULL,details jsonb NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now());
    CREATE TRIGGER atm_events_immutable BEFORE UPDATE OR DELETE ON finance.atm_events
      FOR EACH ROW EXECUTE FUNCTION finance.prevent_mutation();
    GRANT SELECT,INSERT,UPDATE ON finance.atm_runtime,finance.atm_devices,finance.atm_operations TO horizon_finance;
    GRANT SELECT,INSERT ON finance.atm_events TO horizon_finance;
    GRANT USAGE,SELECT ON SEQUENCE finance.atm_events_id_seq TO horizon_finance;
    """)


def downgrade() -> None:
    raise RuntimeError(
        "ATM physical evidence and financial reservations must be preserved"
    )

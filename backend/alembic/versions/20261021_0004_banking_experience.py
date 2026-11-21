"""Add user-facing banking identity, durable transfer confirmations and settings without deleting data."""

from alembic import op

revision = "auth_0004"
down_revision = "auth_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE auth.application_users
      ADD COLUMN bank_username varchar(24),
      ADD COLUMN language varchar(2) NOT NULL DEFAULT 'ru' CHECK(language IN ('ru','en')),
      ADD COLUMN banking_ready boolean NOT NULL DEFAULT false;
    -- Deterministically unique even when legacy UUIDs share the same prefix.
    WITH numbered AS (SELECT id,row_number() OVER(ORDER BY id) AS n FROM auth.application_users)
    UPDATE auth.application_users u SET bank_username='player_'||lpad(to_hex(n.n),17,'0'),banking_ready=true
      FROM numbered n WHERE u.id=n.id;
    CREATE UNIQUE INDEX users_bank_username_unique ON auth.application_users(lower(bank_username));
    ALTER TABLE auth.application_users ADD CONSTRAINT bank_username_format
      CHECK(bank_username IS NULL OR bank_username ~ '^[A-Za-z][A-Za-z0-9_]{2,23}$');
    ALTER TABLE auth.external_identities ADD COLUMN avatar_hash varchar(80);
    CREATE TABLE auth.bank_username_history (
      id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES auth.application_users(id),
      old_name text, new_name text NOT NULL, changed_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TRIGGER bank_username_history_immutable BEFORE UPDATE OR DELETE ON auth.bank_username_history
      FOR EACH ROW EXECUTE FUNCTION auth.prevent_audit_mutation();
    CREATE TABLE auth.saved_recipients (
      id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES auth.application_users(id),
      recipient_user_id uuid REFERENCES auth.application_users(id), account_id uuid,
      label varchar(80) NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(),
      CHECK(num_nonnulls(recipient_user_id,account_id)=1)
    );
    CREATE UNIQUE INDEX saved_recipient_user_unique ON auth.saved_recipients(user_id,recipient_user_id) WHERE recipient_user_id IS NOT NULL;
    CREATE UNIQUE INDEX saved_recipient_account_unique ON auth.saved_recipients(user_id,account_id) WHERE account_id IS NOT NULL;
    CREATE TABLE auth.transfer_quotes (
      id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES auth.application_users(id),
      source_account_id uuid NOT NULL, destination_account_id uuid NOT NULL,
      recipient_user_id uuid REFERENCES auth.application_users(id), snapshot jsonb NOT NULL,
      amount numeric(20,2) NOT NULL CHECK(amount>0 AND amount<=9999999999999999.99),
      reason varchar(500) NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(),
      expires_at timestamptz NOT NULL, operation_id uuid UNIQUE, CHECK(source_account_id<>destination_account_id)
    );
    CREATE INDEX transfer_quotes_recent_idx ON auth.transfer_quotes(user_id,created_at DESC) WHERE operation_id IS NOT NULL;
    CREATE TABLE auth.recent_recipients (
      user_id uuid NOT NULL REFERENCES auth.application_users(id), recipient_key text NOT NULL,
      recipient_user_id uuid REFERENCES auth.application_users(id), account_id uuid NOT NULL,
      last_used_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(user_id,recipient_key)
    );
    CREATE TABLE auth.recipient_lookup_windows (
      user_id uuid PRIMARY KEY REFERENCES auth.application_users(id), window_start timestamptz NOT NULL,
      used integer NOT NULL CHECK(used>0)
    );
    ALTER TABLE auth.notification_preferences ADD COLUMN transfers_enabled boolean NOT NULL DEFAULT true;
    CREATE TABLE auth.user_notifications (
      id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES auth.application_users(id), event_key text NOT NULL,
      kind text NOT NULL, title text NOT NULL, body text NOT NULL, operation_id uuid,
      created_at timestamptz NOT NULL DEFAULT now(), read_at timestamptz, UNIQUE(user_id,event_key)
    );
    CREATE INDEX user_notifications_cursor_idx ON auth.user_notifications(user_id,created_at DESC,id DESC);
    CREATE TABLE auth.minecraft_nickname_history (
      id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES auth.application_users(id), player_uuid uuid NOT NULL,
      nickname varchar(16) NOT NULL, verified_at timestamptz NOT NULL DEFAULT now()
    );
    INSERT INTO auth.minecraft_nickname_history(id,user_id,player_uuid,nickname,verified_at)
      SELECT gen_random_uuid(),user_id,player_uuid,nickname,verified_at FROM auth.minecraft_verified_links;
    CREATE TRIGGER minecraft_nickname_history_immutable BEFORE UPDATE OR DELETE ON auth.minecraft_nickname_history
      FOR EACH ROW EXECUTE FUNCTION auth.prevent_audit_mutation();
    """)


def downgrade() -> None:
    raise RuntimeError(
        "User identities and transfer confirmations require a verified backup, not a destructive automatic downgrade"
    )

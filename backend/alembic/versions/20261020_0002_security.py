"""Revocable opaque sessions, resource-scoped API keys and explicit admin grants."""
from alembic import op

revision="auth_0002"
down_revision="auth_0001"
branch_labels=None
depends_on=None

def upgrade() -> None:
    op.execute("""
    CREATE TABLE auth.user_sessions (
      token_hash char(64) PRIMARY KEY, user_id uuid NOT NULL REFERENCES auth.application_users(id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now(),expires_at timestamptz NOT NULL,revoked_at timestamptz
    );
    CREATE INDEX sessions_active_user_idx ON auth.user_sessions(user_id) WHERE revoked_at IS NULL;
    ALTER TABLE auth.api_keys ADD COLUMN account_ids uuid[] NOT NULL DEFAULT '{}';
    CREATE TABLE auth.user_permissions (
      user_id uuid NOT NULL REFERENCES auth.application_users(id) ON DELETE RESTRICT,
      permission text NOT NULL CHECK(permission IN ('admin.refunds','admin.issuance','admin.audit','admin.investigations','admin.users')),
      granted_by uuid NOT NULL, reason text NOT NULL CHECK(length(reason)>0), created_at timestamptz NOT NULL DEFAULT now(),
      revoked_at timestamptz, PRIMARY KEY(user_id,permission)
    );
    CREATE FUNCTION auth.prevent_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'immutable audit trail' USING ERRCODE='23514'; END; $$;
    CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE ON auth.audit_events
      FOR EACH ROW EXECUTE FUNCTION auth.prevent_audit_mutation();
    CREATE TRIGGER investigation_notes_immutable BEFORE UPDATE OR DELETE ON auth.investigation_notes
      FOR EACH ROW EXECUTE FUNCTION auth.prevent_audit_mutation();
    """)

def downgrade() -> None:
    op.execute("""
    DROP TRIGGER investigation_notes_immutable ON auth.investigation_notes;
    DROP TRIGGER audit_events_immutable ON auth.audit_events;
    DROP FUNCTION auth.prevent_audit_mutation();
    DROP TABLE auth.user_permissions;
    ALTER TABLE auth.api_keys DROP COLUMN account_ids;
    DROP TABLE auth.user_sessions;
    """)

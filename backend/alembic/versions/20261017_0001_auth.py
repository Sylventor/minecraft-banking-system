"""Initial FastAPI-owned auth and integration schema."""
from alembic import op

revision="auth_0001"
down_revision=None
branch_labels=("auth",)
depends_on=None

def upgrade() -> None:
    op.execute("""
    CREATE TABLE auth.application_users (id uuid PRIMARY KEY, status varchar(20) NOT NULL DEFAULT 'active' CHECK(status IN ('active','blocked','archived')), display_name varchar(100) NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE auth.external_identities (id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES auth.application_users(id) ON DELETE RESTRICT, provider varchar(30) NOT NULL, external_id varchar(100) NOT NULL, username varchar(100) NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(provider,external_id));
    CREATE TABLE auth.oauth_states (state_hash varchar(64) PRIMARY KEY, redirect_uri text NOT NULL, expires_at timestamptz NOT NULL, consumed_at timestamptz);
    CREATE TABLE auth.api_keys (id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES auth.application_users(id) ON DELETE RESTRICT, prefix varchar(20) NOT NULL UNIQUE, secret_hash char(64) NOT NULL, name varchar(100) NOT NULL, scopes text[] NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz, expires_at timestamptz);
    CREATE TABLE auth.organizations (id uuid PRIMARY KEY, name varchar(120) NOT NULL UNIQUE, owner_id uuid NOT NULL REFERENCES auth.application_users(id) ON DELETE RESTRICT, created_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE auth.organization_members (id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES auth.organizations(id) ON DELETE RESTRICT, user_id uuid NOT NULL REFERENCES auth.application_users(id) ON DELETE RESTRICT, role varchar(50) NOT NULL CHECK(role IN ('owner','member','administrator')), UNIQUE(organization_id,user_id));
    CREATE TABLE auth.minecraft_links (id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES auth.application_users(id) ON DELETE RESTRICT, nickname varchar(16) NOT NULL UNIQUE, code_hash char(64) NOT NULL, expires_at timestamptz NOT NULL, verified_at timestamptz, attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0));
    CREATE TABLE auth.audit_events (id uuid PRIMARY KEY, actor_id uuid, action varchar(100) NOT NULL, target_type varchar(50) NOT NULL, target_id varchar(100) NOT NULL, metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE auth.investigations (id uuid PRIMARY KEY, status varchar(20) NOT NULL CHECK(status IN ('open','closed')), subject_type varchar(40) NOT NULL, subject_id varchar(100) NOT NULL, created_by uuid NOT NULL REFERENCES auth.application_users(id) ON DELETE RESTRICT, created_at timestamptz NOT NULL DEFAULT now(), closed_at timestamptz);
    CREATE TABLE auth.investigation_notes (id uuid PRIMARY KEY, investigation_id uuid NOT NULL REFERENCES auth.investigations(id) ON DELETE RESTRICT, author_id uuid NOT NULL REFERENCES auth.application_users(id) ON DELETE RESTRICT, body text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE auth.notification_preferences (user_id uuid PRIMARY KEY REFERENCES auth.application_users(id) ON DELETE RESTRICT, discord_enabled boolean NOT NULL DEFAULT true, security_enabled boolean NOT NULL DEFAULT true);
    CREATE TABLE auth.integration_settings (id uuid PRIMARY KEY, organization_id uuid REFERENCES auth.organizations(id) ON DELETE RESTRICT, name varchar(100) NOT NULL, settings jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT now());
    CREATE INDEX api_keys_owner_idx ON auth.api_keys(owner_id) WHERE revoked_at IS NULL;
    CREATE INDEX audit_events_target_idx ON auth.audit_events(target_type,target_id,created_at DESC);
    """)

def downgrade() -> None: op.execute("DROP SCHEMA auth CASCADE")

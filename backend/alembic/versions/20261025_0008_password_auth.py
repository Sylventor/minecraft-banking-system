"""Add optional password credentials without changing users or financial ownership."""

from alembic import op

revision = "auth_0008"
down_revision = "auth_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE auth.password_credentials (
      user_id uuid PRIMARY KEY REFERENCES auth.application_users(id) ON DELETE RESTRICT,
      login varchar(24) NOT NULL UNIQUE CHECK(login ~ '^[a-z][a-z0-9_]{2,23}$'),
      password_hash varchar(256) NOT NULL CHECK(password_hash LIKE '$argon2id$%'),
      updated_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE auth.authentication_limits (
      bucket_hash char(64) PRIMARY KEY, window_start timestamptz NOT NULL, attempts integer NOT NULL CHECK(attempts>0));
    ALTER TABLE auth.user_sessions ADD COLUMN auth_method varchar(10) NOT NULL DEFAULT 'discord'
      CHECK(auth_method IN ('discord','password'));
    ALTER TABLE auth.oauth_states ADD COLUMN purpose varchar(10) NOT NULL DEFAULT 'login'
      CHECK(purpose IN ('login','link','reauth')),
      ADD COLUMN user_id uuid REFERENCES auth.application_users(id) ON DELETE RESTRICT,
      ADD COLUMN session_hash varchar(64),
      ADD CONSTRAINT oauth_link_binding CHECK(purpose='login' OR (user_id IS NOT NULL AND session_hash IS NOT NULL));
    CREATE UNIQUE INDEX external_discord_user_unique ON auth.external_identities(user_id) WHERE provider='discord';
    CREATE INDEX authentication_limits_expiry ON auth.authentication_limits(window_start);
    """)


def downgrade() -> None:
    raise RuntimeError("Removing password credentials would lock out password-only users; restore a verified backup instead")

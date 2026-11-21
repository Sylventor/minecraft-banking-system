"""Separate expiring challenges from identities attested by a trusted Minecraft server."""

from alembic import op

revision = "auth_0003"
down_revision = "auth_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE auth.minecraft_link_challenges (
      id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES auth.application_users(id) ON DELETE RESTRICT,
      code_hash varchar(64) NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT now(),
      expires_at timestamptz NOT NULL, attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 5),
      consumed_at timestamptz, revoked_at timestamptz, CHECK(expires_at>created_at)
    );
    CREATE INDEX minecraft_challenges_user_idx ON auth.minecraft_link_challenges(user_id,created_at DESC);
    CREATE UNIQUE INDEX minecraft_challenges_active_user_idx ON auth.minecraft_link_challenges(user_id)
      WHERE consumed_at IS NULL AND revoked_at IS NULL;
    CREATE TABLE auth.minecraft_verified_links (
      user_id uuid PRIMARY KEY REFERENCES auth.application_users(id) ON DELETE RESTRICT,
      player_uuid uuid NOT NULL UNIQUE,
      nickname varchar(16) NOT NULL CHECK(nickname ~ '^[A-Za-z0-9_]{3,16}$'),
      verified_at timestamptz NOT NULL
    );
    CREATE UNIQUE INDEX minecraft_verified_nickname_idx ON auth.minecraft_verified_links(lower(nickname));
    -- Legacy minecraft_links are preserved but never promoted to a verified identity.
    """)


def downgrade() -> None:
    op.execute("DROP TABLE auth.minecraft_verified_links; DROP TABLE auth.minecraft_link_challenges;")

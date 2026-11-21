"""Persist Ukrainian alongside the existing Russian and English preferences."""

from alembic import op

revision = "auth_0012"
down_revision = "auth_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE auth.application_users DROP CONSTRAINT application_users_language_check;
    ALTER TABLE auth.application_users ALTER COLUMN language TYPE varchar(5);
    ALTER TABLE auth.application_users ADD CONSTRAINT application_users_language_check
      CHECK(language IN ('ru','en','uk-UA'));
    """)


def downgrade() -> None:
    raise RuntimeError("Do not discard saved Ukrainian language preferences")

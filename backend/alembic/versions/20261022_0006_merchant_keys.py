"""Optional organization-bound merchant keys; existing keys retain their scopes."""

from alembic import op

revision = "auth_0006"
down_revision = "auth_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE auth.api_keys ADD COLUMN organization_id uuid REFERENCES auth.organizations(id);
    ALTER TABLE auth.api_keys ADD CHECK(organization_id IS NULL OR (
      cardinality(account_ids)>0 AND scopes <@ ARRAY['payment_requests.create','payment_requests.read',
      'payment_requests.cancel','recipients.read']::text[]));""")


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup; merchant key restrictions must be retained")

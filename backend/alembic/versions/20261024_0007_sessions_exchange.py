"""Persistent rolling sessions and explicit exchange administration; no token secrets."""

from alembic import op

revision = "auth_0007"
down_revision = "auth_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE auth.user_sessions ADD COLUMN public_id uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
      ADD COLUMN last_activity_at timestamptz NOT NULL DEFAULT now(),
      ADD COLUMN absolute_expires_at timestamptz NOT NULL DEFAULT now()+interval '90 days',
      ADD COLUMN client_info varchar(180) NOT NULL DEFAULT '';
    UPDATE auth.user_sessions SET last_activity_at=created_at,absolute_expires_at=created_at+interval '90 days';
    CREATE INDEX sessions_user_active ON auth.user_sessions(user_id,expires_at) WHERE revoked_at IS NULL;
    INSERT INTO auth.permission_registry VALUES ('admin.exchange.read','Просмотр биржевого аудита'),
      ('admin.exchange.manage','Модерация выпусков и торгов');
    INSERT INTO auth.role_permissions SELECT r.id,p.permission FROM auth.admin_roles r
      CROSS JOIN auth.permission_registry p WHERE r.system_key='superadmin' AND p.permission LIKE 'admin.exchange.%';
    """)


def downgrade() -> None:
    raise RuntimeError("Session revocations and exchange audit must be preserved")

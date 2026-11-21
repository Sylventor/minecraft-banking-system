"""Delivered Minecraft challenges and dedicated ATM administration permissions."""

from alembic import op

revision = "auth_0010"
down_revision = "auth_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE auth.minecraft_atm_challenges(
      id uuid PRIMARY KEY,user_id uuid NOT NULL REFERENCES auth.application_users(id),
      nickname varchar(16) NOT NULL,code_hash char(64) NOT NULL,player_uuid uuid,
      authentication text CHECK(authentication IN ('online-mode','server-auth')),
      status text NOT NULL CHECK(status IN ('pending','delivered','failed','cancelled','consumed','expired','locked')),
      attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 5),
      created_at timestamptz NOT NULL DEFAULT now(),expires_at timestamptz NOT NULL,
      delivered_at timestamptz,consumed_at timestamptz);
    CREATE UNIQUE INDEX minecraft_atm_active_user ON auth.minecraft_atm_challenges(user_id)
      WHERE status IN ('pending','delivered');
    CREATE UNIQUE INDEX minecraft_atm_active_nickname ON auth.minecraft_atm_challenges(lower(nickname))
      WHERE status IN ('pending','delivered');
    CREATE INDEX minecraft_atm_rate ON auth.minecraft_atm_challenges(user_id,created_at DESC);
    ALTER TABLE auth.minecraft_verified_links ADD COLUMN authentication text NOT NULL DEFAULT 'online-mode'
      CHECK(authentication IN ('online-mode','server-auth'));
    INSERT INTO auth.permission_registry(permission,description) VALUES
      ('admin.atm.read','Просмотр банкоматов, инкассации и физических операций'),
      ('admin.atm.manage','Управление банкоматами и ручная сверка операций'),
      ('admin.atm.collect','Инкассация ближайшего банкомата через Minecraft');
    INSERT INTO auth.role_permissions(role_id,permission)
      SELECT r.id,p.permission FROM auth.admin_roles r CROSS JOIN auth.permission_registry p
      WHERE r.system_key='superadmin' AND p.permission LIKE 'admin.atm.%';
    """)


def downgrade() -> None:
    raise RuntimeError("Minecraft verification and ATM audit must be preserved")

"""Official news and an explicit publishing permission; no financial changes."""

from alembic import op

revision = "auth_0009"
down_revision = "auth_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE auth.news (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      title varchar(180) NOT NULL CHECK(length(btrim(title)) BETWEEN 1 AND 180),
      slug varchar(180) NOT NULL UNIQUE CHECK(slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
      summary varchar(500) NOT NULL DEFAULT '',
      content text NOT NULL CHECK(octet_length(content) BETWEEN 1 AND 131072),
      category text NOT NULL CHECK(category IN ('general','updates','maintenance','important','rules','exchange')),
      cover_image varchar(2048), author_id uuid NOT NULL REFERENCES auth.application_users(id) ON DELETE RESTRICT,
      status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published')),
      is_pinned boolean NOT NULL DEFAULT false,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      published_at timestamptz, deleted_at timestamptz,
      CHECK(status<>'published' OR published_at IS NOT NULL));
    CREATE INDEX news_published_order ON auth.news(is_pinned DESC,published_at DESC,id DESC)
      WHERE status='published' AND deleted_at IS NULL;
    CREATE INDEX news_published_category ON auth.news(category,is_pinned DESC,published_at DESC,id DESC)
      WHERE status='published' AND deleted_at IS NULL;
    CREATE INDEX news_admin_order ON auth.news(updated_at DESC,id DESC) WHERE deleted_at IS NULL;
    INSERT INTO auth.permission_registry(permission,description) VALUES ('admin.news.manage','Управление новостями');
    INSERT INTO auth.role_permissions(role_id,permission)
      SELECT id,'admin.news.manage' FROM auth.admin_roles WHERE system_key='superadmin';
    """)


def downgrade() -> None:
    raise RuntimeError("News and publication audit must be preserved; use a verified backup for rollback")

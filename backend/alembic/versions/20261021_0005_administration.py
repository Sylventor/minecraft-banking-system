"""Add compatible RBAC, delivery outbox and versioned terms; preserve all existing grants."""

from alembic import op

revision = "auth_0005"
down_revision = "auth_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE auth.permission_registry(permission text PRIMARY KEY,description text NOT NULL);
    INSERT INTO auth.permission_registry VALUES
      ('admin.overview','Обзор банка'),('admin.users.read','Просмотр пользователей'),
      ('admin.users','Управление пользователями'),('admin.accounts.read','Просмотр счетов'),
      ('admin.transactions.read','Просмотр транзакций'),('admin.audit','Просмотр аудита'),
      ('admin.investigations.read','Просмотр расследований'),('admin.investigations','Ведение расследований'),
      ('admin.refunds','Возвраты: дополнительно нужны полномочия ядра'),
      ('admin.issuance','Эмиссия: дополнительно нужны полномочия ядра'),
      ('admin.finance.manage','Управление финансовыми разрешениями: дополнительно нужны полномочия ядра'),
      ('admin.accounts.manage','Статусы счетов: дополнительно нужны полномочия ядра'),
      ('admin.roles.manage','Управление административными ролями'),('admin.terms.manage','Публикация правил');
    ALTER TABLE auth.user_permissions DROP CONSTRAINT user_permissions_permission_check;
    ALTER TABLE auth.user_permissions ADD CONSTRAINT user_permissions_registry_fk
      FOREIGN KEY(permission) REFERENCES auth.permission_registry(permission);
    CREATE TABLE auth.admin_roles(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),name varchar(80) NOT NULL,
      description varchar(500) NOT NULL DEFAULT '',system_key text UNIQUE,
      created_by uuid REFERENCES auth.application_users(id),created_at timestamptz NOT NULL DEFAULT now());
    CREATE UNIQUE INDEX admin_role_name_unique ON auth.admin_roles(lower(name));
    CREATE TABLE auth.role_permissions(role_id uuid NOT NULL REFERENCES auth.admin_roles(id),
      permission text NOT NULL REFERENCES auth.permission_registry(permission),PRIMARY KEY(role_id,permission));
    CREATE TABLE auth.user_roles(user_id uuid NOT NULL REFERENCES auth.application_users(id),
      role_id uuid NOT NULL REFERENCES auth.admin_roles(id),granted_by uuid NOT NULL REFERENCES auth.application_users(id),
      reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 500),created_at timestamptz NOT NULL DEFAULT now(),
      revoked_at timestamptz,PRIMARY KEY(user_id,role_id));
    INSERT INTO auth.admin_roles(name,description,system_key) VALUES
      ('Superadmin','Полное административное управление; финансовые полномочия ядра выдаются отдельно','superadmin'),
      ('Administrator','Пользователи, счета, расследования и правила; без эмиссии и финансового делегирования','administrator'),
      ('Financial Manager','Эмиссия и возвраты при наличии отдельных разрешений ядра','financial_manager'),
      ('Auditor','Просмотр финансовых данных и аудита','auditor'),
      ('Support','Просмотр профилей пользователей','support');
    INSERT INTO auth.role_permissions SELECT id,permission FROM auth.admin_roles CROSS JOIN auth.permission_registry WHERE system_key='superadmin';
    INSERT INTO auth.role_permissions SELECT id,unnest(ARRAY['admin.overview','admin.users.read','admin.users',
      'admin.accounts.read','admin.accounts.manage','admin.transactions.read','admin.audit',
      'admin.investigations.read','admin.investigations','admin.terms.manage']) FROM auth.admin_roles WHERE system_key='administrator';
    INSERT INTO auth.role_permissions SELECT id,unnest(ARRAY['admin.overview','admin.users.read','admin.accounts.read',
      'admin.transactions.read','admin.issuance','admin.refunds']) FROM auth.admin_roles WHERE system_key='financial_manager';
    INSERT INTO auth.role_permissions SELECT id,unnest(ARRAY['admin.overview','admin.users.read','admin.accounts.read',
      'admin.transactions.read','admin.audit','admin.investigations.read']) FROM auth.admin_roles WHERE system_key='auditor';
    INSERT INTO auth.role_permissions SELECT id,'admin.users.read' FROM auth.admin_roles WHERE system_key='support';
    CREATE VIEW auth.effective_permissions AS
      SELECT user_id,permission FROM auth.user_permissions WHERE revoked_at IS NULL UNION
      SELECT u.user_id,p.permission FROM auth.user_roles u JOIN auth.role_permissions p ON p.role_id=u.role_id WHERE u.revoked_at IS NULL;
    ALTER TABLE auth.notification_preferences
      ADD COLUMN incoming_enabled boolean NOT NULL DEFAULT true,
      ADD COLUMN outgoing_enabled boolean NOT NULL DEFAULT true,
      ADD COLUMN organizations_enabled boolean NOT NULL DEFAULT true;
    CREATE TABLE auth.discord_outbox(
      id uuid PRIMARY KEY,notification_id uuid NOT NULL UNIQUE REFERENCES auth.user_notifications(id),
      user_id uuid NOT NULL REFERENCES auth.application_users(id),category text NOT NULL,
      critical boolean NOT NULL DEFAULT false,payload jsonb NOT NULL,
      status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','sent','failed','suppressed')),
      attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 6),
      available_at timestamptz NOT NULL DEFAULT now(),lease_token uuid,lease_until timestamptz,
      last_error varchar(80),message_id varchar(30),created_at timestamptz NOT NULL DEFAULT now(),sent_at timestamptz);
    CREATE INDEX discord_outbox_due ON auth.discord_outbox(available_at,id) WHERE status IN ('pending','sending');
    CREATE TABLE auth.notification_worker_status(id boolean PRIMARY KEY DEFAULT true CHECK(id),last_seen_at timestamptz NOT NULL);
    CREATE TABLE auth.terms_versions(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),version bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
      markdown text NOT NULL CHECK(octet_length(markdown) BETWEEN 1 AND 131072),
      author_id uuid NOT NULL REFERENCES auth.application_users(id),published_at timestamptz NOT NULL DEFAULT now(),
      status text NOT NULL CHECK(status IN ('published','archived','unpublished')),
      restored_from uuid REFERENCES auth.terms_versions(id));
    CREATE UNIQUE INDEX one_published_terms ON auth.terms_versions(status) WHERE status='published';
    CREATE FUNCTION auth.protect_terms_content() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'terms history is immutable'; END IF;
      IF (NEW.id,NEW.version,NEW.markdown,NEW.author_id,NEW.published_at,NEW.restored_from) IS DISTINCT FROM
         (OLD.id,OLD.version,OLD.markdown,OLD.author_id,OLD.published_at,OLD.restored_from) THEN
        RAISE EXCEPTION 'terms content is immutable'; END IF; RETURN NEW; END; $$;
    CREATE TRIGGER terms_content_immutable BEFORE UPDATE OR DELETE ON auth.terms_versions
      FOR EACH ROW EXECUTE FUNCTION auth.protect_terms_content();
    -- Future acceptance references a specific immutable version; no acceptance is required in 2.2.
    CREATE TABLE auth.terms_acceptances(user_id uuid REFERENCES auth.application_users(id),
      version_id uuid REFERENCES auth.terms_versions(id),accepted_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(user_id,version_id));
    """)


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup; administrative grants and publication history must be preserved")

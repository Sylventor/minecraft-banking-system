"""Private support conversations, operator notes and read watermarks."""

from alembic import op

revision = "auth_0011"
down_revision = "auth_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE auth.support_tickets(
      id uuid PRIMARY KEY,number bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
      user_id uuid NOT NULL REFERENCES auth.application_users(id),request_id uuid NOT NULL,
      title varchar(160) NOT NULL,category text NOT NULL CHECK(category IN
        ('account','payments','atm','organizations','exchange','website','feedback','other')),
      status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','working','waiting','resolved','closed')),
      assigned_to uuid REFERENCES auth.application_users(id),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      closed_at timestamptz,UNIQUE(user_id,request_id));
    CREATE INDEX support_ticket_user ON auth.support_tickets(user_id,updated_at DESC);
    CREATE INDEX support_ticket_queue ON auth.support_tickets(status,category,updated_at DESC);
    CREATE TABLE auth.support_messages(
      id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      ticket_id uuid NOT NULL REFERENCES auth.support_tickets(id),
      author_id uuid NOT NULL REFERENCES auth.application_users(id),client_id uuid,
      sender_kind text NOT NULL CHECK(sender_kind IN ('user','staff','system')),
      body varchar(4000) NOT NULL CHECK(length(body)>0),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(author_id,client_id));
    CREATE INDEX support_message_history ON auth.support_messages(ticket_id,id);
    CREATE INDEX support_message_rate ON auth.support_messages(author_id,created_at DESC);
    CREATE TABLE auth.support_internal_notes(
      id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      ticket_id uuid NOT NULL REFERENCES auth.support_tickets(id),
      author_id uuid NOT NULL REFERENCES auth.application_users(id),client_id uuid NOT NULL,
      body varchar(4000) NOT NULL CHECK(length(body)>0),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(author_id,client_id));
    CREATE TABLE auth.support_reads(
      ticket_id uuid NOT NULL REFERENCES auth.support_tickets(id),user_id uuid NOT NULL REFERENCES auth.application_users(id),
      last_message_id bigint NOT NULL DEFAULT 0,updated_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(ticket_id,user_id));
    INSERT INTO auth.permission_registry(permission,description) VALUES
      ('admin.support.read','Просмотр обращений и внутренних заметок поддержки'),
      ('admin.support.reply','Ответы и внутренние заметки поддержки'),
      ('admin.support.manage','Статусы и назначение сотрудников поддержки');
    INSERT INTO auth.role_permissions(role_id,permission)
      SELECT r.id,p.permission FROM auth.admin_roles r CROSS JOIN auth.permission_registry p
      WHERE r.system_key='superadmin' AND p.permission LIKE 'admin.support.%';
    """)


def downgrade() -> None:
    raise RuntimeError("Support correspondence and audit must be preserved")

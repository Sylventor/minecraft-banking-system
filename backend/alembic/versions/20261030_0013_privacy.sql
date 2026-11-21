ALTER TABLE auth.application_users ADD COLUMN age_confirmed_at timestamptz,
  ADD COLUMN erased_at timestamptz, ADD COLUMN processing_restricted_at timestamptz;
ALTER TABLE auth.oauth_states ADD COLUMN age_confirmed boolean NOT NULL DEFAULT false;

CREATE TABLE auth.privacy_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES auth.application_users(id),
  kind text NOT NULL CHECK(kind IN ('access','rectification','erasure','restriction','objection','portability','consent_withdrawal')),
  source text NOT NULL CHECK(source IN ('support','email')),
  ticket_id uuid REFERENCES auth.support_tickets(id),
  status text NOT NULL DEFAULT 'received' CHECK(status IN ('received','verified','approved','rejected','processing','blocked','completed')),
  created_by uuid NOT NULL REFERENCES auth.application_users(id),
  verified_by uuid REFERENCES auth.application_users(id),verified_at timestamptz,
  decided_by uuid REFERENCES auth.application_users(id),decided_at timestamptz,
  reason varchar(1000) NOT NULL DEFAULT '',retention_basis varchar(1000) NOT NULL DEFAULT '',
  review_confirmed boolean NOT NULL DEFAULT false,
  notification_status text NOT NULL DEFAULT 'pending' CHECK(notification_status IN ('pending','manual_required','notified','unavailable')),
  result jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  due_at timestamptz NOT NULL DEFAULT (clock_timestamp()+interval '1 month'),
  completed_at timestamptz,
  CHECK(verified_at IS NULL OR verified_by IS NOT NULL),
  CHECK(status IN ('received','rejected') OR verified_at IS NOT NULL)
);
CREATE INDEX privacy_request_queue ON auth.privacy_requests(status,created_at);
CREATE TABLE auth.privacy_holds (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),user_id uuid NOT NULL REFERENCES auth.application_users(id),
  ticket_id uuid REFERENCES auth.support_tickets(id),
  basis varchar(1000) NOT NULL CHECK(length(trim(basis))>0),
  expires_at timestamptz NOT NULL,created_by uuid NOT NULL REFERENCES auth.application_users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),released_at timestamptz,
  CHECK(expires_at>created_at)
);
CREATE INDEX privacy_hold_subject ON auth.privacy_holds(user_id,expires_at) WHERE released_at IS NULL;
CREATE TABLE auth.privacy_device_tasks(
  case_id uuid PRIMARY KEY REFERENCES auth.privacy_requests(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES auth.application_users(id),nicknames text[] NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),acknowledged_at timestamptz,
  blocked boolean NOT NULL DEFAULT false
);
INSERT INTO auth.permission_registry(permission,description) VALUES
  ('admin.privacy.read','Просмотр запросов о персональных данных'),
  ('admin.privacy.manage','Проверка заявителя, решения и ограничения обработки'),
  ('admin.privacy.export','Экспорт персональных данных для проверки сотрудником'),
  ('admin.privacy.erase','Подтверждение удаления персональных данных');
INSERT INTO auth.role_permissions(role_id,permission)
  SELECT r.id,p.permission FROM auth.admin_roles r CROSS JOIN auth.permission_registry p
  WHERE r.system_key='superadmin' AND p.permission LIKE 'admin.privacy.%';

CREATE FUNCTION auth.privacy_has_hold(subject uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS(SELECT 1 FROM auth.privacy_holds WHERE user_id=subject AND released_at IS NULL AND expires_at>clock_timestamp())
    OR EXISTS(SELECT 1 FROM auth.privacy_requests WHERE user_id=subject AND status NOT IN ('completed','rejected'))
$$;

CREATE OR REPLACE FUNCTION auth.prevent_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE j jsonb:=to_jsonb(OLD); subject uuid; erased boolean;
BEGIN
  subject:=COALESCE(j->>'user_id',j->>'author_id',j->>'actor_id')::uuid;
  SELECT EXISTS(SELECT 1 FROM auth.application_users WHERE id=subject AND erased_at IS NOT NULL) INTO erased;
  IF TG_OP='DELETE' AND TG_TABLE_NAME IN ('bank_username_history','minecraft_nickname_history') AND erased THEN RETURN OLD; END IF;
  IF TG_OP='DELETE' AND TG_TABLE_NAME='minecraft_nickname_history'
    AND NOT EXISTS(SELECT 1 FROM auth.minecraft_verified_links WHERE user_id=subject) THEN RETURN OLD; END IF;
  IF TG_OP='DELETE' AND TG_TABLE_NAME='audit_events' AND (j->>'created_at')::timestamptz<clock_timestamp()-interval '365 days'
    AND NOT auth.privacy_has_hold(subject) AND NOT EXISTS(SELECT 1 FROM auth.investigations WHERE status='open')
    AND NOT EXISTS(SELECT 1 FROM auth.privacy_requests WHERE user_id::text=j->>'target_id' AND status NOT IN ('completed','rejected'))
    AND NOT EXISTS(SELECT 1 FROM auth.privacy_holds WHERE user_id::text=j->>'target_id' AND released_at IS NULL AND expires_at>now())
    THEN RETURN OLD; END IF;
  IF TG_OP='UPDATE' AND TG_TABLE_NAME='audit_events' AND to_jsonb(NEW)->'metadata_json'='{}'::jsonb
    AND (to_jsonb(NEW)-'metadata_json')=(j-'metadata_json')
    AND (erased OR EXISTS(SELECT 1 FROM auth.application_users WHERE id::text=j->>'target_id' AND erased_at IS NOT NULL)
      OR (j->>'action' LIKE 'minecraft.link.%' AND NOT EXISTS(SELECT 1 FROM auth.minecraft_verified_links WHERE user_id=subject)))
    THEN RETURN NEW; END IF;
  IF TG_OP='UPDATE' AND TG_TABLE_NAME='investigation_notes' AND to_jsonb(NEW)->>'body'='[redacted]'
    AND (to_jsonb(NEW)-'body')=(j-'body') AND erased
    AND NOT EXISTS(SELECT 1 FROM auth.investigations WHERE status='open') THEN RETURN NEW; END IF;
  RAISE EXCEPTION 'immutable audit trail' USING ERRCODE='23514';
END $$;

"""Transactional confirmed events and explicitly delegated financial administration."""

from alembic import op

revision = "finance_0005"
down_revision = "finance_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE finance.authorities DROP CONSTRAINT authorities_permission_check;
    ALTER TABLE finance.authorities ADD CONSTRAINT authorities_permission_check CHECK(permission IN
      ('issue.create','refund.create','admin.read','authority.manage','account.state.manage'));
    CREATE TABLE finance.event_outbox(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      event_key text NOT NULL UNIQUE,principal_id uuid NOT NULL,category text NOT NULL,
      payload jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),acknowledged_at timestamptz);
    CREATE INDEX finance_events_pending ON finance.event_outbox(id) WHERE acknowledged_at IS NULL;
    CREATE FUNCTION finance.operation_notification() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE a record; recipient record; direction text;
    BEGIN
      IF NEW.status NOT IN ('completed','failed','reversed') THEN RETURN NEW; END IF;
      FOR a IN SELECT * FROM finance.accounts WHERE id IN (NEW.source_account_id,NEW.destination_account_id)
        AND account_type<>'system' LOOP
        direction := CASE WHEN a.id=NEW.destination_account_id THEN 'incoming' ELSE 'outgoing' END;
        FOR recipient IN
          SELECT a.owner_principal_id user_id WHERE a.owner_principal_id IS NOT NULL UNION
          SELECT m.principal_id FROM finance.organization_members m WHERE m.organization_id=a.organization_id
            AND m.revoked_at IS NULL AND (m.role IN ('owner','administrator') OR 'history.read'=ANY(m.permissions))
        LOOP
          INSERT INTO finance.event_outbox(event_key,principal_id,category,payload) VALUES
            ('operation:'||NEW.id||':'||a.id||':'||recipient.user_id,recipient.user_id,
             CASE WHEN a.organization_id IS NOT NULL THEN 'organizations' ELSE direction END,
             jsonb_build_object('operation_id',NEW.id,'type',NEW.operation_type,'status',NEW.status,'amount',NEW.amount::text,
               'currency','DB','direction',direction,'account_id',a.id,'account',a.name,'public_number',a.public_number,
               'source_owner_id',(SELECT owner_principal_id FROM finance.accounts WHERE id=NEW.source_account_id),
               'destination_owner_id',(SELECT owner_principal_id FROM finance.accounts WHERE id=NEW.destination_account_id),
               'visible_accounts',(SELECT jsonb_agg(jsonb_build_object('name',b.name,
                 'direction',CASE WHEN b.id=NEW.destination_account_id THEN 'incoming' ELSE 'outgoing' END,
                 'category',CASE WHEN b.organization_id IS NOT NULL THEN 'organizations'
                   WHEN b.id=NEW.destination_account_id THEN 'incoming' ELSE 'outgoing' END)
                 ORDER BY (b.id=NEW.destination_account_id)) FROM finance.accounts b
                 WHERE b.id IN (NEW.source_account_id,NEW.destination_account_id) AND b.account_type<>'system'
                   AND (b.owner_principal_id=recipient.user_id OR EXISTS(SELECT 1 FROM finance.organization_members m
                     WHERE m.organization_id=b.organization_id AND m.principal_id=recipient.user_id AND m.revoked_at IS NULL
                       AND (m.role IN ('owner','administrator') OR 'history.read'=ANY(m.permissions))))),
               'organization_id',a.organization_id,'date',COALESCE(NEW.completed_at,NEW.created_at)))
            ON CONFLICT(event_key) DO NOTHING;
        END LOOP;
      END LOOP;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER operation_notification_outbox AFTER INSERT ON finance.financial_operations
      FOR EACH ROW EXECUTE FUNCTION finance.operation_notification();
    CREATE FUNCTION finance.account_notification() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE recipient record; kind text;
    BEGIN
      IF TG_OP='UPDATE' AND NEW.status=OLD.status THEN RETURN NEW; END IF;
      kind := CASE WHEN TG_OP='INSERT' THEN 'account.created' ELSE 'account.status' END;
      FOR recipient IN SELECT NEW.owner_principal_id user_id WHERE NEW.owner_principal_id IS NOT NULL UNION
        SELECT m.principal_id FROM finance.organization_members m WHERE m.organization_id=NEW.organization_id
          AND m.revoked_at IS NULL AND (m.role IN ('owner','administrator') OR 'history.read'=ANY(m.permissions)) LOOP
        INSERT INTO finance.event_outbox(event_key,principal_id,category,payload) VALUES
          (kind||':'||gen_random_uuid(),recipient.user_id,'security',jsonb_build_object('type',kind,
            'account',NEW.name,'public_number',NEW.public_number,'status',NEW.status,'date',now()));
      END LOOP; RETURN NEW;
    END; $$;
    CREATE TRIGGER account_notification_outbox AFTER INSERT OR UPDATE OF status ON finance.accounts
      FOR EACH ROW EXECUTE FUNCTION finance.account_notification();
    CREATE FUNCTION finance.member_notification() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE kind text;
    BEGIN
      IF TG_OP='UPDATE' AND (NEW.role,NEW.permissions,NEW.revoked_at) IS NOT DISTINCT FROM
        (OLD.role,OLD.permissions,OLD.revoked_at) THEN RETURN NEW; END IF;
      kind := CASE WHEN NEW.revoked_at IS NOT NULL THEN 'organization.removed'
        WHEN TG_OP='INSERT' THEN 'organization.invited' ELSE 'organization.role' END;
      INSERT INTO finance.event_outbox(event_key,principal_id,category,payload) VALUES
        (kind||':'||gen_random_uuid(),NEW.principal_id,'organizations',jsonb_build_object('type',kind,
          'organization',(SELECT name FROM finance.organizations WHERE id=NEW.organization_id),'role',NEW.role,'date',now()));
      RETURN NEW;
    END; $$;
    CREATE TRIGGER member_notification_outbox AFTER INSERT OR UPDATE ON finance.organization_members
      FOR EACH ROW EXECUTE FUNCTION finance.member_notification();
    """)


def downgrade() -> None:
    raise RuntimeError("Confirmed notification history requires a verified backup")

"""Core-owned requests; settlement and the existing ledger share one transaction."""

from alembic import op

revision = "finance_0006"
down_revision = "finance_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    SET CONSTRAINTS ALL IMMEDIATE;
    ALTER TABLE finance.account_permissions DROP CONSTRAINT account_permissions_permission_check;
    ALTER TABLE finance.account_permissions ADD CHECK(permission IN (
      'accounts.read','balance.read','history.read','transfer','payment.create','hold.create',
      'refund.create','issue.create','account.manage',
      'payment_requests.create','payment_requests.read','payment_requests.cancel'));
    ALTER TABLE finance.organization_members DROP CONSTRAINT organization_members_permissions_check;
    ALTER TABLE finance.organization_members ADD CHECK(permissions <@ ARRAY[
      'accounts.read','balance.read','history.read','transfer','payment.create','hold.create','account.manage',
      'payment_requests.create','payment_requests.read','payment_requests.cancel']::text[]);
    UPDATE finance.organization_members SET permissions=permissions||ARRAY[
      'payment_requests.create','payment_requests.read','payment_requests.cancel']::text[]
      WHERE role IN ('owner','administrator');
    INSERT INTO finance.account_permissions(account_id,principal_id,permission,granted_by)
      SELECT a.id,p.principal_id,x.permission,p.granted_by FROM finance.accounts a
      JOIN finance.account_permissions p ON p.account_id=a.id AND p.permission='account.manage' AND p.revoked_at IS NULL
      CROSS JOIN unnest(ARRAY['payment_requests.create','payment_requests.read','payment_requests.cancel']) x(permission)
      WHERE a.account_type<>'system' ON CONFLICT DO NOTHING;
    CREATE TABLE finance.payment_requests(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), creator_id uuid NOT NULL, creator_key_id uuid,
      organization_id uuid REFERENCES finance.organizations(id), payer_id uuid NOT NULL,
      recipient_account_id uuid NOT NULL REFERENCES finance.accounts(id),
      amount numeric(18,2) NOT NULL CHECK(amount>0),currency text NOT NULL DEFAULT 'DB' CHECK(currency='DB'),
      description text NOT NULL CHECK(length(description) BETWEEN 1 AND 500),
      status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','paid','declined','cancelled','expired')),
      external_id varchar(120),metadata jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(metadata)='object' AND octet_length(metadata::text)<=8192),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(), expires_at timestamptz NOT NULL,
      paid_at timestamptz, cancelled_at timestamptz, declined_at timestamptz,
      transaction_id uuid UNIQUE REFERENCES finance.financial_operations(id), source_account_id uuid REFERENCES finance.accounts(id),
      creation_key text NOT NULL CHECK(length(creation_key) BETWEEN 16 AND 255), fingerprint text NOT NULL,
      pay_key text, CHECK(creator_id<>payer_id), CHECK(expires_at>created_at),
      CHECK(status='paid' OR (transaction_id IS NULL AND paid_at IS NULL AND source_account_id IS NULL AND pay_key IS NULL)),
      CHECK(status<>'paid' OR pay_key IS NOT NULL),
      CHECK((status='paid')=(transaction_id IS NOT NULL AND paid_at IS NOT NULL AND source_account_id IS NOT NULL)),
      CHECK((status='cancelled')=(cancelled_at IS NOT NULL)), CHECK((status='declined')=(declined_at IS NOT NULL)),
      UNIQUE(creator_id,creation_key)
    );
    CREATE UNIQUE INDEX payment_request_order_unique ON finance.payment_requests(recipient_account_id,external_id)
      WHERE external_id IS NOT NULL;
    CREATE INDEX payment_requests_payer_page ON finance.payment_requests(payer_id,created_at DESC,id DESC);
    CREATE INDEX payment_requests_recipient_page ON finance.payment_requests(recipient_account_id,created_at DESC,id DESC);
    CREATE INDEX payment_requests_creator_page ON finance.payment_requests(creator_id,created_at DESC,id DESC);
    CREATE INDEX payment_requests_expiry ON finance.payment_requests(expires_at) WHERE status='pending';
    CREATE FUNCTION finance.payment_request_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'payment requests cannot be deleted'; END IF;
      IF (to_jsonb(OLD)-ARRAY['status','paid_at','transaction_id','source_account_id','pay_key','cancelled_at','declined_at'])
         IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['status','paid_at','transaction_id','source_account_id','pay_key','cancelled_at','declined_at'])
         OR OLD.status<>'pending' OR NEW.status='pending' THEN
        RAISE EXCEPTION 'immutable payment request or invalid transition';
      END IF;
      IF NEW.status='paid' AND NOT EXISTS(SELECT 1 FROM finance.financial_operations o
        JOIN finance.accounts a ON a.id=o.source_account_id WHERE o.id=NEW.transaction_id
        AND o.status='completed' AND o.operation_type='payment' AND o.initiator_id=NEW.payer_id
        AND o.source_account_id=NEW.source_account_id AND a.owner_principal_id=NEW.payer_id AND a.account_type='personal'
        AND o.destination_account_id=NEW.recipient_account_id AND o.amount=NEW.amount) THEN
        RAISE EXCEPTION 'settlement must match a confirmed ledger payment';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER payment_request_guard BEFORE UPDATE OR DELETE ON finance.payment_requests
      FOR EACH ROW EXECUTE FUNCTION finance.payment_request_guard();
    CREATE FUNCTION finance.payment_request_event() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE who uuid; merchant text; category text;
    BEGIN
      INSERT INTO finance.profile_events(actor_id,action,resource_id,metadata)
        VALUES(COALESCE(NULLIF(current_setting('horizon.actor_id',true),'')::uuid,NEW.payer_id),'payment_request.'||NEW.status,NEW.id,
          jsonb_build_object('transaction_id',NEW.transaction_id));
      SELECT COALESCE(o.name,a.name) INTO merchant FROM finance.accounts a
        LEFT JOIN finance.organizations o ON o.id=a.organization_id WHERE a.id=NEW.recipient_account_id;
      FOR who IN SELECT NEW.payer_id UNION SELECT p.principal_id FROM finance.account_permissions p
        WHERE p.account_id=NEW.recipient_account_id AND p.permission='payment_requests.read' AND p.revoked_at IS NULL
      LOOP
        category:=CASE WHEN who=NEW.payer_id THEN 'incoming' ELSE 'organizations' END;
        INSERT INTO finance.event_outbox(event_key,principal_id,category,payload)
          VALUES('payment-request:'||NEW.id||':'||NEW.status||':'||who,who,category,
          jsonb_build_object('type','payment_request.'||NEW.status,'payment_request_id',NEW.id,
            'status',NEW.status,'amount',NEW.amount::text,'currency','DB','description',NEW.description,
            'merchant',merchant,'expires_at',NEW.expires_at,'date',clock_timestamp()));
      END LOOP;
      RETURN NEW;
    END $$;
    CREATE TRIGGER payment_request_event AFTER INSERT OR UPDATE ON finance.payment_requests
      FOR EACH ROW EXECUTE FUNCTION finance.payment_request_event();
    SET CONSTRAINTS ALL DEFERRED;
    """)


def downgrade() -> None:
    raise RuntimeError(
        "Restore a verified backup; payment evidence must not be discarded"
    )

"""Organization analytics annotations and durable payroll, without rewriting financial evidence."""

from alembic import op

revision = "finance_0007"
down_revision = "finance_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    SET CONSTRAINTS ALL IMMEDIATE;
    ALTER TABLE finance.account_permissions DROP CONSTRAINT account_permissions_permission_check;
    ALTER TABLE finance.account_permissions ADD CHECK(permission IN (
      'accounts.read','balance.read','history.read','transfer','payment.create','hold.create','refund.create','issue.create',
      'account.manage','payment_requests.create','payment_requests.read','payment_requests.cancel',
      'analytics.read','analytics.export','analytics.categorize','payroll.read','payroll.employees.manage',
      'payroll.salaries.manage','payroll.batches.create','payroll.approve','payroll.execute','payroll.automatic.manage'));
    ALTER TABLE finance.organization_members DROP CONSTRAINT organization_members_permissions_check;
    ALTER TABLE finance.organization_members ADD CHECK(permissions <@ ARRAY[
      'accounts.read','balance.read','history.read','transfer','payment.create','hold.create','account.manage',
      'payment_requests.create','payment_requests.read','payment_requests.cancel',
      'analytics.read','analytics.export','analytics.categorize','payroll.read','payroll.employees.manage',
      'payroll.salaries.manage','payroll.batches.create','payroll.approve','payroll.execute','payroll.automatic.manage']::text[]);
    UPDATE finance.organization_members SET permissions=permissions||ARRAY[
      'analytics.read','analytics.export','analytics.categorize','payroll.read','payroll.employees.manage',
      'payroll.salaries.manage','payroll.batches.create','payroll.approve','payroll.execute','payroll.automatic.manage']::text[]
      WHERE role IN ('owner','administrator') AND revoked_at IS NULL;
    INSERT INTO finance.account_permissions(account_id,principal_id,permission,granted_by)
      SELECT a.id,m.principal_id,x.permission,o.owner_id FROM finance.accounts a
      JOIN finance.organizations o ON o.id=a.organization_id JOIN finance.organization_members m ON m.organization_id=o.id
      CROSS JOIN unnest(ARRAY['analytics.read','analytics.export','analytics.categorize','payroll.read','payroll.employees.manage',
      'payroll.salaries.manage','payroll.batches.create','payroll.approve','payroll.execute','payroll.automatic.manage']) x(permission)
      WHERE m.role IN ('owner','administrator') AND m.revoked_at IS NULL ON CONFLICT DO NOTHING;
    CREATE TABLE finance.business_categories(
      organization_id uuid NOT NULL REFERENCES finance.organizations(id),key text NOT NULL,
      name varchar(80) NOT NULL CHECK(length(name)>0),PRIMARY KEY(organization_id,key),UNIQUE(organization_id,name));
    CREATE FUNCTION finance.business_categories_init() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      INSERT INTO finance.business_categories SELECT NEW.id,k,n FROM (VALUES
        ('sales','Продажи'),('payroll','Зарплаты'),('rent','Аренда'),('purchases','Закупки'),
        ('services','Услуги'),('investment','Инвестиции'),('other','Прочее')) v(k,n);
      RETURN NEW;
    END $$;
    CREATE TRIGGER business_categories_init AFTER INSERT ON finance.organizations
      FOR EACH ROW EXECUTE FUNCTION finance.business_categories_init();
    INSERT INTO finance.business_categories SELECT o.id,k,n FROM finance.organizations o CROSS JOIN (VALUES
      ('sales','Продажи'),('payroll','Зарплаты'),('rent','Аренда'),('purchases','Закупки'),
      ('services','Услуги'),('investment','Инвестиции'),('other','Прочее')) v(k,n);
    CREATE TABLE finance.operation_categories(
      organization_id uuid NOT NULL,operation_id uuid NOT NULL REFERENCES finance.financial_operations(id),
      category_key text NOT NULL,changed_by uuid NOT NULL,updated_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(organization_id,operation_id), FOREIGN KEY(organization_id,category_key)
        REFERENCES finance.business_categories(organization_id,key));
    CREATE TABLE finance.payroll_employees(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES finance.organizations(id),
      user_id uuid NOT NULL,account_id uuid NOT NULL REFERENCES finance.accounts(id),salary numeric(18,2) NOT NULL CHECK(salary>0),
      frequency text NOT NULL CHECK(frequency IN ('weekly','biweekly','monthly')),
      position varchar(100) NOT NULL DEFAULT '',note varchar(500) NOT NULL DEFAULT '',active boolean NOT NULL DEFAULT true,
      revision integer NOT NULL DEFAULT 1,created_by uuid NOT NULL,updated_by uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),UNIQUE(organization_id,user_id));
    CREATE TABLE finance.payroll_schedules(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES finance.organizations(id),
      source_account_id uuid NOT NULL REFERENCES finance.accounts(id),name varchar(120) NOT NULL,
      frequency text NOT NULL CHECK(frequency IN ('weekly','biweekly','monthly')),weekday integer NOT NULL CHECK(weekday BETWEEN 1 AND 7),
      month_day integer NOT NULL CHECK(month_day BETWEEN 1 AND 31),local_time time NOT NULL,timezone text NOT NULL,
      mode text NOT NULL CHECK(mode IN ('manual','automatic')),status text NOT NULL DEFAULT 'paused' CHECK(status IN ('active','paused','disabled')),
      max_amount numeric(18,2) NOT NULL CHECK(max_amount>0),next_run_at timestamptz NOT NULL,
      created_by uuid NOT NULL,approved_by uuid,approved_at timestamptz,revision integer NOT NULL DEFAULT 1,
      created_at timestamptz NOT NULL DEFAULT now(),last_error text,
      CHECK(mode<>'automatic' OR status<>'active' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL AND approved_by<>created_by)));
    CREATE INDEX payroll_schedules_due ON finance.payroll_schedules(next_run_at) WHERE status='active';
    CREATE TABLE finance.payroll_schedule_items(
      schedule_id uuid NOT NULL REFERENCES finance.payroll_schedules(id),employee_id uuid NOT NULL REFERENCES finance.payroll_employees(id),
      employee_revision integer NOT NULL,user_id uuid NOT NULL,account_id uuid NOT NULL REFERENCES finance.accounts(id),
      amount numeric(18,2) NOT NULL CHECK(amount>0),PRIMARY KEY(schedule_id,employee_id),UNIQUE(schedule_id,user_id));
    CREATE TABLE finance.payroll_batches(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES finance.organizations(id),
      source_account_id uuid NOT NULL REFERENCES finance.accounts(id),description varchar(450) NOT NULL,
      status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','pending_approval','processing','completed','partially_completed','failed','cancelled')),
      total numeric(18,2) NOT NULL CHECK(total>0),fee numeric(18,2) NOT NULL DEFAULT 0 CHECK(fee=0),
      created_by uuid NOT NULL,approved_by uuid,approved_at timestamptz,executor_id uuid,
      creation_key text NOT NULL,fingerprint text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),completed_at timestamptz,
      schedule_id uuid REFERENCES finance.payroll_schedules(id),scheduled_for timestamptz,schedule_revision integer,
      UNIQUE(organization_id,creation_key),UNIQUE(schedule_id,scheduled_for),
      CHECK(approved_by IS NULL OR approved_by<>created_by),
      CHECK(status NOT IN ('processing','completed','partially_completed','failed') OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)));
    CREATE INDEX payroll_batches_page ON finance.payroll_batches(organization_id,created_at DESC,id DESC);
    CREATE TABLE finance.payroll_items(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),batch_id uuid NOT NULL REFERENCES finance.payroll_batches(id),
      employee_id uuid NOT NULL REFERENCES finance.payroll_employees(id),user_id uuid NOT NULL,
      destination_account_id uuid NOT NULL REFERENCES finance.accounts(id),amount numeric(18,2) NOT NULL CHECK(amount>0),
      status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','failed')),
      operation_id uuid UNIQUE REFERENCES finance.financial_operations(id),paid_at timestamptz,error_code text,
      UNIQUE(batch_id,employee_id),UNIQUE(batch_id,user_id),
      CHECK((status='completed')=(operation_id IS NOT NULL AND paid_at IS NOT NULL)));
    CREATE INDEX payroll_items_pending ON finance.payroll_items(batch_id,status);
    CREATE FUNCTION finance.payroll_item_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' OR OLD.status='completed' OR
        (to_jsonb(OLD)-ARRAY['status','operation_id','paid_at','error_code']) IS DISTINCT FROM
        (to_jsonb(NEW)-ARRAY['status','operation_id','paid_at','error_code']) THEN
        RAISE EXCEPTION 'immutable payroll evidence'; END IF;
      IF NEW.status='completed' AND NOT EXISTS(SELECT 1 FROM finance.financial_operations o
        JOIN finance.payroll_batches b ON b.id=NEW.batch_id WHERE o.id=NEW.operation_id AND o.status='completed'
        AND o.source='payroll' AND o.operation_type='transfer' AND o.source_account_id=b.source_account_id
        AND o.destination_account_id=NEW.destination_account_id AND o.amount=NEW.amount) THEN
        RAISE EXCEPTION 'payroll requires matching confirmed ledger operation'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER payroll_item_guard BEFORE UPDATE OR DELETE ON finance.payroll_items
      FOR EACH ROW EXECUTE FUNCTION finance.payroll_item_guard();
    CREATE FUNCTION finance.payroll_batch_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' OR (to_jsonb(OLD)-ARRAY['status','approved_by','approved_at','executor_id','completed_at'])
        IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['status','approved_by','approved_at','executor_id','completed_at']) THEN
        RAISE EXCEPTION 'immutable payroll batch snapshot'; END IF;
      IF NOT ((OLD.status='draft' AND NEW.status IN ('pending_approval','cancelled')) OR
        (OLD.status='pending_approval' AND NEW.status IN ('pending_approval','processing','cancelled')) OR
        (OLD.status='processing' AND NEW.status IN ('completed','partially_completed','failed')) OR
        (OLD.status IN ('failed','partially_completed') AND NEW.status='processing')) THEN
        RAISE EXCEPTION 'invalid payroll batch transition'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER payroll_batch_guard BEFORE UPDATE OR DELETE ON finance.payroll_batches
      FOR EACH ROW EXECUTE FUNCTION finance.payroll_batch_guard();
    CREATE FUNCTION finance.business_require(actor uuid,org uuid,perm text) RETURNS void LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM 1 FROM finance.organization_members WHERE organization_id=org AND principal_id=actor
        AND revoked_at IS NULL AND perm=ANY(permissions) FOR SHARE;
      IF NOT FOUND THEN RAISE insufficient_privilege USING MESSAGE='organization permission is not granted'; END IF;
    END $$;
    CREATE FUNCTION finance.business_audit(actor uuid,action text,target uuid,data jsonb DEFAULT '{}') RETURNS void LANGUAGE sql AS $$
      INSERT INTO finance.profile_events(actor_id,action,resource_id,metadata) VALUES(actor,'business.'||action,target,data)
    $$;
    CREATE FUNCTION finance.payroll_notice(org uuid,kind text,target uuid,data jsonb DEFAULT '{}') RETURNS void LANGUAGE sql AS $$
      INSERT INTO finance.event_outbox(event_key,principal_id,category,payload)
        SELECT 'payroll:'||gen_random_uuid(),m.principal_id,'organizations',
          jsonb_build_object('type','payroll.'||kind,'organization_id',org,'organization',o.name,'target_id',target,'date',clock_timestamp())||data
        FROM finance.organization_members m JOIN finance.organizations o ON o.id=m.organization_id
        WHERE m.organization_id=org AND m.revoked_at IS NULL AND 'payroll.read'=ANY(m.permissions)
    $$;
    CREATE FUNCTION finance.payroll_employee_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE sched record;
    BEGIN
      IF TG_OP='UPDATE' THEN
        PERFORM finance.business_audit(NEW.updated_by,'employee.update',NEW.id,
          jsonb_build_object('old_salary',OLD.salary::text,'new_salary',NEW.salary::text,'old_account',OLD.account_id,'new_account',NEW.account_id));
        FOR sched IN UPDATE finance.payroll_schedules s SET status='paused',approved_by=NULL,approved_at=NULL,
          last_error='employee_changed',revision=revision+1 WHERE s.id IN
          (SELECT schedule_id FROM finance.payroll_schedule_items WHERE employee_id=NEW.id) AND s.status<>'disabled' RETURNING s.* LOOP
          PERFORM finance.payroll_notice(sched.organization_id,'schedule_paused',sched.id,jsonb_build_object('reason','employee_changed'));
        END LOOP;
      ELSE PERFORM finance.business_audit(NEW.created_by,'employee.create',NEW.id,jsonb_build_object('salary',NEW.salary::text)); END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER payroll_employee_changed AFTER INSERT OR UPDATE ON finance.payroll_employees
      FOR EACH ROW EXECUTE FUNCTION finance.payroll_employee_changed();
    DROP TRIGGER operation_notification_outbox ON finance.financial_operations;
    CREATE TRIGGER operation_notification_outbox AFTER INSERT ON finance.financial_operations
      FOR EACH ROW WHEN (NEW.source<>'payroll') EXECUTE FUNCTION finance.operation_notification();
    SET CONSTRAINTS ALL DEFERRED;
    """)


def downgrade() -> None:
    raise RuntimeError(
        "Restore a verified backup; payroll evidence must not be removed"
    )

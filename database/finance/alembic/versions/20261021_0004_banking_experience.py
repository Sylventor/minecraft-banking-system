"""Add public account metadata, primary accounts and core-owned organization grants without rewriting money."""

from alembic import op

revision = "finance_0004"
down_revision = "finance_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    -- Earlier revisions may have queued deferred checks in the same Alembic transaction.
    -- Execute them before DDL; do not disable any integrity trigger.
    SET CONSTRAINTS ALL IMMEDIATE;
    ALTER TABLE finance.accounts
      ADD COLUMN public_number text NOT NULL DEFAULT ('HB-'||upper(substr(md5(gen_random_uuid()::text),1,16))),
      ADD COLUMN name text NOT NULL DEFAULT 'Личный счёт' CHECK(length(name) BETWEEN 1 AND 80),
      ADD COLUMN owner_principal_id uuid,
      ADD COLUMN organization_id uuid;
    ALTER TABLE finance.accounts ADD CONSTRAINT accounts_public_number_unique UNIQUE(public_number);
    ALTER TABLE finance.accounts ADD CONSTRAINT accounts_public_number_format CHECK(public_number ~ '^HB-[A-F0-9]{16}$');
    UPDATE finance.accounts SET name=CASE account_type WHEN 'system' THEN 'Системный счёт'
      WHEN 'organization' THEN 'Счёт организации' WHEN 'technical' THEN 'Технический счёт' ELSE name END;
    -- Only unambiguous, self-granted legacy personal managers establish ownership.
    UPDATE finance.accounts a SET owner_principal_id=c.principal_id FROM (
      SELECT account_id,min(principal_id::text)::uuid principal_id FROM finance.account_permissions
      WHERE permission='account.manage' AND revoked_at IS NULL AND granted_by=principal_id
      GROUP BY account_id HAVING count(*)=1
    ) c WHERE a.id=c.account_id AND a.account_type='personal';
    CREATE TABLE finance.primary_accounts (
      principal_id uuid PRIMARY KEY, account_id uuid NOT NULL UNIQUE REFERENCES finance.accounts(id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    ALTER TABLE finance.accounts ADD CONSTRAINT account_owner_identity UNIQUE(owner_principal_id,id);
    ALTER TABLE finance.primary_accounts ADD CONSTRAINT primary_account_owner_fk
      FOREIGN KEY(principal_id,account_id) REFERENCES finance.accounts(owner_principal_id,id) ON DELETE RESTRICT;
    CREATE TABLE finance.account_creation_keys (
      principal_id uuid NOT NULL, request_key text NOT NULL, account_id uuid NOT NULL REFERENCES finance.accounts(id),
      fingerprint text NOT NULL, PRIMARY KEY(principal_id,request_key)
    );
    CREATE INDEX accounts_owner_idx ON finance.accounts(owner_principal_id) WHERE owner_principal_id IS NOT NULL;
    CREATE TABLE finance.organizations (
      id uuid PRIMARY KEY, owner_id uuid NOT NULL, name text NOT NULL CHECK(length(name) BETWEEN 2 AND 120),
      created_at timestamptz NOT NULL DEFAULT now()
    );
    ALTER TABLE finance.accounts ADD CONSTRAINT accounts_organization_fk
      FOREIGN KEY(organization_id) REFERENCES finance.organizations(id) ON DELETE RESTRICT;
    CREATE INDEX accounts_organization_idx ON finance.accounts(organization_id) WHERE organization_id IS NOT NULL;
    CREATE TABLE finance.organization_members (
      organization_id uuid NOT NULL REFERENCES finance.organizations(id), principal_id uuid NOT NULL,
      role text NOT NULL CHECK(role IN ('owner','administrator','member')),
      permissions text[] NOT NULL DEFAULT '{}', revoked_at timestamptz,
      PRIMARY KEY(organization_id,principal_id),
      CHECK(permissions <@ ARRAY['accounts.read','balance.read','history.read','transfer','payment.create','hold.create','account.manage']::text[])
    );
    CREATE TABLE finance.profile_events (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), actor_id uuid NOT NULL, action text NOT NULL,
      resource_id uuid NOT NULL, metadata jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TRIGGER profile_events_immutable BEFORE UPDATE OR DELETE ON finance.profile_events
      FOR EACH ROW EXECUTE FUNCTION finance.prevent_mutation();
    SET CONSTRAINTS ALL DEFERRED;
    """)


def downgrade() -> None:
    raise RuntimeError(
        "Public account identities and ownership must not be automatically downgraded; restore a verified backup instead"
    )

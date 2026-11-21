"""Core-owned, atomically settled securities and bank-account reservations."""

from alembic import op

revision = "finance_0008"
down_revision = "finance_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE finance.authorities DROP CONSTRAINT authorities_permission_check;
    ALTER TABLE finance.authorities ADD CONSTRAINT authorities_permission_check CHECK(permission IN
      ('issue.create','refund.create','admin.read','authority.manage','account.state.manage','exchange.manage'));
    ALTER TABLE finance.account_permissions DROP CONSTRAINT account_permissions_permission_check;
    ALTER TABLE finance.account_permissions ADD CHECK(permission IN (
      'accounts.read','balance.read','history.read','transfer','payment.create','hold.create','refund.create','issue.create','account.manage',
      'payment_requests.create','payment_requests.read','payment_requests.cancel','analytics.read','analytics.export','analytics.categorize',
      'payroll.read','payroll.employees.manage','payroll.salaries.manage','payroll.batches.create','payroll.approve','payroll.execute','payroll.automatic.manage',
      'exchange.trade','exchange.issuer.manage','exchange.dividends.manage'));
    ALTER TABLE finance.organization_members DROP CONSTRAINT organization_members_permissions_check;
    ALTER TABLE finance.organization_members ADD CHECK(permissions <@ ARRAY[
      'accounts.read','balance.read','history.read','transfer','payment.create','hold.create','account.manage',
      'payment_requests.create','payment_requests.read','payment_requests.cancel','analytics.read','analytics.export','analytics.categorize',
      'payroll.read','payroll.employees.manage','payroll.salaries.manage','payroll.batches.create','payroll.approve','payroll.execute','payroll.automatic.manage',
      'exchange.trade','exchange.issuer.manage','exchange.dividends.manage']::text[]);
    UPDATE finance.organization_members SET permissions=permissions||ARRAY['exchange.trade','exchange.issuer.manage','exchange.dividends.manage']
      WHERE role IN ('owner','administrator') AND revoked_at IS NULL;
    INSERT INTO finance.account_permissions(account_id,principal_id,permission,granted_by)
      SELECT a.id,a.owner_principal_id,'exchange.trade',a.owner_principal_id FROM finance.accounts a
      WHERE a.account_type='personal' AND a.owner_principal_id IS NOT NULL ON CONFLICT DO NOTHING;
    INSERT INTO finance.account_permissions(account_id,principal_id,permission,granted_by)
      SELECT a.id,m.principal_id,p,o.owner_id FROM finance.accounts a JOIN finance.organizations o ON o.id=a.organization_id
      JOIN finance.organization_members m ON m.organization_id=o.id
      CROSS JOIN unnest(ARRAY['exchange.trade','exchange.issuer.manage','exchange.dividends.manage']) p
      WHERE m.role IN ('owner','administrator') AND m.revoked_at IS NULL ON CONFLICT DO NOTHING;
    CREATE TABLE finance.exchange_stocks(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES finance.organizations(id),
      treasury_account_id uuid NOT NULL REFERENCES finance.accounts(id),ticker varchar(10) NOT NULL UNIQUE CHECK(ticker ~ '^[A-Z][A-Z0-9]{1,9}$'),
      name varchar(120) NOT NULL,description varchar(2000) NOT NULL,terms varchar(4000) NOT NULL,
      rights varchar(2000) NOT NULL,total_quantity bigint NOT NULL CHECK(total_quantity BETWEEN 1 AND 1000000000),
      status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','pending_review','approved','rejected','active','suspended','delisted')),
      created_by uuid NOT NULL,approved_by uuid,created_at timestamptz NOT NULL DEFAULT now(),reason varchar(500) NOT NULL DEFAULT '');
    CREATE TABLE finance.exchange_issuances(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),stock_id uuid NOT NULL REFERENCES finance.exchange_stocks(id),
      quantity bigint NOT NULL CHECK(quantity>0),actor_id uuid NOT NULL,reason varchar(500) NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp());
    CREATE TABLE finance.exchange_positions(
      stock_id uuid NOT NULL REFERENCES finance.exchange_stocks(id),account_id uuid NOT NULL REFERENCES finance.accounts(id),
      quantity bigint NOT NULL DEFAULT 0 CHECK(quantity>=0),reserved bigint NOT NULL DEFAULT 0 CHECK(reserved BETWEEN 0 AND quantity),
      cost_cents numeric(38,0) NOT NULL DEFAULT 0 CHECK(cost_cents>=0),realized_cents numeric(38,0) NOT NULL DEFAULT 0,
      dividends_cents numeric(38,0) NOT NULL DEFAULT 0,PRIMARY KEY(stock_id,account_id));
    CREATE TABLE finance.exchange_share_entries(
      id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,stock_id uuid NOT NULL REFERENCES finance.exchange_stocks(id),
      account_id uuid NOT NULL REFERENCES finance.accounts(id),quantity bigint NOT NULL CHECK(quantity<>0),
      event_id uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(event_id,account_id));
    CREATE INDEX exchange_shares_history ON finance.exchange_share_entries(stock_id,created_at,account_id);
    CREATE TABLE finance.exchange_ipos(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),stock_id uuid NOT NULL UNIQUE REFERENCES finance.exchange_stocks(id),
      quantity bigint NOT NULL CHECK(quantity>0),remaining bigint NOT NULL CHECK(remaining BETWEEN 0 AND quantity),
      price numeric(18,2) NOT NULL CHECK(price>0),starts_at timestamptz NOT NULL,ends_at timestamptz NOT NULL CHECK(ends_at>starts_at),
      max_per_buyer bigint NOT NULL DEFAULT 1000000000 CHECK(max_per_buyer>0),status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')));
    CREATE TABLE finance.exchange_orders(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
      stock_id uuid NOT NULL REFERENCES finance.exchange_stocks(id),actor_id uuid NOT NULL,account_id uuid NOT NULL REFERENCES finance.accounts(id),
      side text NOT NULL CHECK(side IN ('BUY','SELL')),kind text NOT NULL CHECK(kind IN ('limit','market')),
      quantity bigint NOT NULL CHECK(quantity BETWEEN 1 AND 1000000000),remaining bigint NOT NULL CHECK(remaining BETWEEN 0 AND quantity),
      price numeric(18,2) NOT NULL CHECK(price>0),status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','partially_filled','filled','cancelled','expired')),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),expires_at timestamptz NOT NULL);
    CREATE INDEX exchange_orderbook ON finance.exchange_orders(stock_id,side,price,sequence) WHERE status IN ('open','partially_filled');
    CREATE INDEX exchange_order_owner ON finance.exchange_orders(actor_id,sequence DESC);
    CREATE TABLE finance.exchange_reserves(
      order_id uuid PRIMARY KEY REFERENCES finance.exchange_orders(id),account_id uuid NOT NULL REFERENCES finance.accounts(id),
      amount numeric(18,2) NOT NULL CHECK(amount>=0));
    CREATE TABLE finance.exchange_trades(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
      stock_id uuid NOT NULL REFERENCES finance.exchange_stocks(id),buy_order_id uuid REFERENCES finance.exchange_orders(id),
      sell_order_id uuid REFERENCES finance.exchange_orders(id),ipo_id uuid REFERENCES finance.exchange_ipos(id),
      buyer_account_id uuid NOT NULL REFERENCES finance.accounts(id),seller_account_id uuid NOT NULL REFERENCES finance.accounts(id),
      quantity bigint NOT NULL CHECK(quantity>0),price numeric(18,2) NOT NULL CHECK(price>0),
      total numeric(18,2) NOT NULL CHECK(total=quantity*price),fee numeric(18,2) NOT NULL DEFAULT 0 CHECK(fee=0),
      operation_id uuid NOT NULL UNIQUE REFERENCES finance.financial_operations(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK(buyer_account_id<>seller_account_id));
    CREATE INDEX exchange_trade_candles ON finance.exchange_trades(stock_id,created_at,sequence);
    CREATE TABLE finance.exchange_dividends(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),stock_id uuid NOT NULL REFERENCES finance.exchange_stocks(id),
      source_account_id uuid NOT NULL REFERENCES finance.accounts(id),created_by uuid NOT NULL,approved_by uuid,
      record_at timestamptz NOT NULL,pay_at timestamptz NOT NULL CHECK(pay_at>=record_at),
      amount numeric(18,2) NOT NULL CHECK(amount>0),mode text NOT NULL CHECK(mode IN ('total','per_share')),
      status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','approved','processing','completed','partially_completed','failed')),
      total numeric(18,2) NOT NULL DEFAULT 0,rounding_remainder numeric(18,2) NOT NULL DEFAULT 0,
      executor_id uuid,created_at timestamptz NOT NULL DEFAULT now(),CHECK(approved_by IS NULL OR approved_by<>created_by));
    CREATE TABLE finance.exchange_distributions(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),dividend_id uuid NOT NULL REFERENCES finance.exchange_dividends(id),
      account_id uuid NOT NULL REFERENCES finance.accounts(id),quantity bigint NOT NULL CHECK(quantity>0),
      amount numeric(18,2) NOT NULL CHECK(amount>0),status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','failed')),
      operation_id uuid UNIQUE REFERENCES finance.financial_operations(id),error_code text,
      UNIQUE(dividend_id,account_id),CHECK((status='completed')=(operation_id IS NOT NULL)));
    CREATE TABLE finance.exchange_requests(actor_id uuid NOT NULL,action text NOT NULL,key text NOT NULL,
      fingerprint text NOT NULL,response jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(actor_id,action,key));
    CREATE FUNCTION finance.exchange_asset_check() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE sid uuid; a record; supply bigint; actual bigint; expected bigint;
    BEGIN
      sid:=NEW.stock_id;
      SELECT COALESCE(sum(quantity),0) INTO supply FROM finance.exchange_issuances WHERE stock_id=sid;
      SELECT COALESCE(sum(quantity),0) INTO actual FROM finance.exchange_positions WHERE stock_id=sid;
      IF supply<>actual THEN RAISE EXCEPTION 'securities supply mismatch' USING ERRCODE='23514'; END IF;
      FOR a IN SELECT * FROM finance.exchange_positions WHERE stock_id=sid LOOP
        SELECT COALESCE(sum(quantity),0) INTO expected FROM finance.exchange_share_entries WHERE stock_id=sid AND account_id=a.account_id;
        IF expected<>a.quantity THEN RAISE EXCEPTION 'share journal mismatch' USING ERRCODE='23514'; END IF;
        SELECT COALESCE(sum(remaining),0) INTO expected FROM finance.exchange_orders WHERE stock_id=sid AND account_id=a.account_id
          AND side='SELL' AND status IN ('open','partially_filled');
        SELECT expected+COALESCE(sum(i.remaining),0) INTO expected FROM finance.exchange_ipos i JOIN finance.exchange_stocks s ON s.id=i.stock_id
          WHERE i.stock_id=sid AND s.treasury_account_id=a.account_id AND i.status='open';
        IF expected<>a.reserved THEN RAISE EXCEPTION 'share reserve mismatch' USING ERRCODE='23514'; END IF;
      END LOOP;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER exchange_positions_consistent AFTER INSERT OR UPDATE ON finance.exchange_positions
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.exchange_asset_check();
    CREATE CONSTRAINT TRIGGER exchange_issuance_check AFTER INSERT ON finance.exchange_issuances
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.exchange_asset_check();
    CREATE CONSTRAINT TRIGGER exchange_entries_check AFTER INSERT ON finance.exchange_share_entries
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.exchange_asset_check();
    CREATE CONSTRAINT TRIGGER exchange_order_check AFTER INSERT OR UPDATE ON finance.exchange_orders
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.exchange_asset_check();
    CREATE CONSTRAINT TRIGGER exchange_ipo_check AFTER INSERT OR UPDATE ON finance.exchange_ipos
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.exchange_asset_check();
    CREATE FUNCTION finance.exchange_trade_check() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM finance.financial_operations o WHERE o.id=NEW.operation_id AND o.status='completed'
        AND o.source='exchange' AND o.operation_type='transfer' AND o.source_account_id=NEW.buyer_account_id
        AND o.destination_account_id=NEW.seller_account_id AND o.amount=NEW.total) THEN
        RAISE EXCEPTION 'trade requires matching bank ledger evidence' USING ERRCODE='23514'; END IF;
      IF NOT EXISTS(SELECT 1 FROM finance.exchange_share_entries WHERE event_id=NEW.id AND account_id=NEW.buyer_account_id AND quantity=NEW.quantity)
        OR NOT EXISTS(SELECT 1 FROM finance.exchange_share_entries WHERE event_id=NEW.id AND account_id=NEW.seller_account_id AND quantity=-NEW.quantity) THEN
        RAISE EXCEPTION 'trade requires matching share journal evidence' USING ERRCODE='23514'; END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER exchange_trade_check AFTER INSERT ON finance.exchange_trades
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.exchange_trade_check();
    CREATE OR REPLACE FUNCTION finance.check_account_cache() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE expected numeric; reserved numeric; actual numeric; actual_held numeric;
    BEGIN
      SELECT balance,held INTO actual,actual_held FROM finance.accounts WHERE id=NEW.id;
      SELECT COALESCE(sum(CASE direction WHEN 'credit' THEN amount ELSE -amount END),0) INTO expected FROM finance.ledger_entries WHERE account_id=NEW.id;
      SELECT COALESCE(sum(amount),0) INTO reserved FROM finance.holds WHERE account_id=NEW.id AND status='active';
      SELECT reserved+COALESCE(sum(amount),0) INTO reserved FROM finance.exchange_reserves WHERE account_id=NEW.id;
      IF actual<>expected OR actual_held<>reserved THEN RAISE EXCEPTION 'bank ledger/reserve mismatch' USING ERRCODE='23514'; END IF;
      RETURN NULL;
    END $$;
    CREATE OR REPLACE VIEW finance.reconciliation AS SELECT a.id,a.balance,a.held,
      COALESCE((SELECT sum(CASE e.direction WHEN 'credit' THEN e.amount ELSE -e.amount END) FROM finance.ledger_entries e WHERE e.account_id=a.id),0) ledger_balance,
      COALESCE((SELECT sum(h.amount) FROM finance.holds h WHERE h.account_id=a.id AND h.status='active'),0)+
      COALESCE((SELECT sum(r.amount) FROM finance.exchange_reserves r WHERE r.account_id=a.id),0) active_holds FROM finance.accounts a;
    CREATE FUNCTION finance.exchange_reserve_check() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE aid uuid;
    BEGIN
      aid:=COALESCE(NEW.account_id,OLD.account_id);
      IF EXISTS(SELECT 1 FROM finance.accounts a WHERE a.id=aid AND a.held<>((
        SELECT COALESCE(sum(amount),0) FROM finance.holds WHERE account_id=aid AND status='active')+(
        SELECT COALESCE(sum(amount),0) FROM finance.exchange_reserves WHERE account_id=aid))) THEN
        RAISE EXCEPTION 'exchange reserve/cache mismatch' USING ERRCODE='23514'; END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER exchange_reserve_check AFTER INSERT OR UPDATE OR DELETE ON finance.exchange_reserves
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.exchange_reserve_check();
    CREATE TRIGGER exchange_trades_immutable BEFORE UPDATE OR DELETE ON finance.exchange_trades FOR EACH ROW EXECUTE FUNCTION finance.prevent_mutation();
    CREATE TRIGGER exchange_entries_immutable BEFORE UPDATE OR DELETE ON finance.exchange_share_entries FOR EACH ROW EXECUTE FUNCTION finance.prevent_mutation();
    CREATE TRIGGER exchange_issuances_immutable BEFORE UPDATE OR DELETE ON finance.exchange_issuances FOR EACH ROW EXECUTE FUNCTION finance.prevent_mutation();
    CREATE FUNCTION finance.exchange_distribution_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' OR OLD.status='completed' OR (to_jsonb(OLD)-ARRAY['status','operation_id','error_code']) IS DISTINCT FROM
        (to_jsonb(NEW)-ARRAY['status','operation_id','error_code']) THEN RAISE EXCEPTION 'immutable dividend snapshot'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER exchange_distribution_guard BEFORE UPDATE OR DELETE ON finance.exchange_distributions
      FOR EACH ROW EXECUTE FUNCTION finance.exchange_distribution_guard();
    CREATE FUNCTION finance.exchange_distribution_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF (SELECT status FROM finance.exchange_dividends WHERE id=NEW.dividend_id)<>'draft' THEN
        RAISE EXCEPTION 'dividend snapshot is already sealed' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER exchange_distribution_insert_guard BEFORE INSERT ON finance.exchange_distributions
      FOR EACH ROW EXECUTE FUNCTION finance.exchange_distribution_insert_guard();
    CREATE FUNCTION finance.exchange_distribution_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.status='completed' AND NOT EXISTS(SELECT 1 FROM finance.financial_operations o
        JOIN finance.exchange_dividends d ON d.id=NEW.dividend_id WHERE o.id=NEW.operation_id
        AND o.status='completed' AND o.source='exchange' AND o.operation_type='transfer'
        AND o.source_account_id=d.source_account_id AND o.destination_account_id=NEW.account_id AND o.amount=NEW.amount) THEN
        RAISE EXCEPTION 'dividend requires matching cash evidence' USING ERRCODE='23514'; END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER exchange_distribution_evidence AFTER INSERT OR UPDATE ON finance.exchange_distributions
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.exchange_distribution_evidence();
    CREATE FUNCTION finance.exchange_dividend_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' OR OLD.status='completed' OR
        (to_jsonb(OLD)-ARRAY['status','approved_by','total','rounding_remainder','executor_id']) IS DISTINCT FROM
        (to_jsonb(NEW)-ARRAY['status','approved_by','total','rounding_remainder','executor_id']) OR
        NOT ((OLD.status='draft' AND NEW.status='approved') OR (OLD.status='approved' AND NEW.status='completed')) THEN
        RAISE EXCEPTION 'immutable dividend declaration' USING ERRCODE='23514'; END IF;
      IF NEW.total<>(SELECT COALESCE(sum(amount),0) FROM finance.exchange_distributions WHERE dividend_id=NEW.id) OR
        (NEW.status='completed' AND EXISTS(SELECT 1 FROM finance.exchange_distributions WHERE dividend_id=NEW.id AND status<>'completed')) THEN
        RAISE EXCEPTION 'dividend total or completion mismatch' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER exchange_dividend_guard BEFORE UPDATE OR DELETE ON finance.exchange_dividends
      FOR EACH ROW EXECUTE FUNCTION finance.exchange_dividend_guard();
    CREATE FUNCTION finance.exchange_approved_supply() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.status IN ('approved','active','suspended','delisted') AND NEW.total_quantity<>
        (SELECT COALESCE(sum(quantity),0) FROM finance.exchange_issuances WHERE stock_id=NEW.id) THEN
        RAISE EXCEPTION 'approved share supply mismatch' USING ERRCODE='23514'; END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER exchange_approved_supply AFTER INSERT OR UPDATE ON finance.exchange_stocks
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.exchange_approved_supply();
    CREATE FUNCTION finance.exchange_order_reserve_check() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE oid uuid; o record; reserved numeric;
    BEGIN
      IF TG_TABLE_NAME='exchange_orders' THEN oid:=NEW.id; ELSE oid:=COALESCE(NEW.order_id,OLD.order_id); END IF;
      SELECT * INTO o FROM finance.exchange_orders WHERE id=oid;
      SELECT COALESCE(sum(amount),0) INTO reserved FROM finance.exchange_reserves WHERE order_id=oid;
      IF o.side='BUY' AND o.kind='limit' AND o.status IN ('open','partially_filled') AND reserved<>o.remaining*o.price
        OR (o.status NOT IN ('open','partially_filled') AND reserved<>0)
        OR (o.kind='market' AND o.status IN ('open','partially_filled')) THEN
        RAISE EXCEPTION 'unsecured or non-final market order' USING ERRCODE='23514'; END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER exchange_order_cash_consistent AFTER INSERT OR UPDATE ON finance.exchange_orders
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.exchange_order_reserve_check();
    CREATE CONSTRAINT TRIGGER exchange_order_reserve_consistent AFTER INSERT OR UPDATE OR DELETE ON finance.exchange_reserves
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.exchange_order_reserve_check();
    DROP TRIGGER operation_notification_outbox ON finance.financial_operations;
    CREATE TRIGGER operation_notification_outbox AFTER INSERT ON finance.financial_operations
      FOR EACH ROW WHEN (NEW.source NOT IN ('payroll','exchange')) EXECUTE FUNCTION finance.operation_notification();
    """)


def downgrade() -> None:
    raise RuntimeError(
        "Never remove securities or settlement evidence; restore a verified backup"
    )

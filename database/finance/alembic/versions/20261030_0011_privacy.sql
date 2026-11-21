-- Upgrade adds controls only. Retention/erasure are separate, backup-gated jobs.
CREATE TABLE finance.privacy_subjects(
  user_id uuid PRIMARY KEY,status text NOT NULL CHECK(status IN ('restricted','erasing','erased')),
  retention_basis varchar(1000) NOT NULL DEFAULT '',updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE finance.privacy_holds(
  user_id uuid PRIMARY KEY,expires_at timestamptz NOT NULL,basis varchar(1000) NOT NULL CHECK(length(trim(basis))>0)
);

CREATE FUNCTION finance.privacy_has_hold(subject uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS(SELECT 1 FROM finance.privacy_holds WHERE user_id=subject AND expires_at>clock_timestamp())
$$;

CREATE FUNCTION finance.privacy_scope(subject uuid) RETURNS text[] LANGUAGE sql STABLE AS $$
  WITH ac AS (SELECT id FROM finance.accounts WHERE owner_principal_id=subject),
  ops AS (SELECT id FROM finance.financial_operations WHERE initiator_id=subject
    OR source_account_id IN (SELECT id FROM ac) OR destination_account_id IN (SELECT id FROM ac))
  SELECT ARRAY[subject::text]||COALESCE(ARRAY(SELECT id::text FROM ac),'{}')
    ||COALESCE(ARRAY(SELECT id::text FROM finance.organizations WHERE owner_id=subject),'{}')
    ||COALESCE(ARRAY(SELECT id::text FROM ops),'{}')
    ||COALESCE(ARRAY(SELECT id::text FROM finance.atm_operations WHERE user_id=subject),'{}')
    ||COALESCE(ARRAY(SELECT id::text FROM finance.payment_requests WHERE payer_id=subject OR creator_id=subject),'{}')
    ||COALESCE(ARRAY(SELECT id::text FROM finance.payroll_batches WHERE created_by=subject OR approved_by=subject),'{}')
    ||COALESCE(ARRAY(SELECT id::text FROM finance.ledger_transactions WHERE operation_id IN (SELECT id FROM ops)),'{}')
$$;

CREATE FUNCTION finance.privacy_export(subject uuid,tab text,off integer DEFAULT 0) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE result jsonb; patterns text[];
BEGIN
  IF off<0 OR off>10000000 OR NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='finance' AND c.relname=tab AND c.relkind='r' AND tab<>'alembic_version')
    THEN RAISE EXCEPTION 'invalid privacy export page'; END IF;
  SELECT array_agg('%'||x||'%') INTO patterns FROM unnest(finance.privacy_scope(subject)) x;
  EXECUTE format('SELECT jsonb_build_object(''rows'',COALESCE(jsonb_agg(j),''[]''::jsonb),''next_offset'',CASE WHEN count(*)=100 THEN $3+100 END)
    FROM (SELECT to_jsonb(t)-ARRAY[''secret_hash'',''token_hash'',''code_hash'',''fingerprint'',''request_fingerprint'',''idempotency_key'',''key''] j
    FROM finance.%I t WHERE to_jsonb(t)::text LIKE ANY($1) ORDER BY to_jsonb(t)::text OFFSET $2 LIMIT 100) p',tab)
    INTO result USING patterns,off,off;
  RETURN result;
END $$;

CREATE FUNCTION finance.privacy_row_has_hold(rowdata jsonb) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS(SELECT 1 FROM finance.privacy_holds h WHERE h.expires_at>clock_timestamp()
    AND rowdata::text LIKE ANY(SELECT '%'||v||'%' FROM unnest(finance.privacy_scope(h.user_id)) v))
$$;

CREATE FUNCTION finance.privacy_blockers(subject uuid) RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT jsonb_strip_nulls(jsonb_build_object(
    'legal_hold',CASE WHEN finance.privacy_has_hold(subject) THEN true END,
    'account_funds',NULLIF((SELECT count(*) FROM finance.accounts WHERE owner_principal_id=subject AND (balance<>0 OR held<>0)),0),
    'organization_ownership',NULLIF((SELECT count(*) FROM finance.organizations WHERE owner_id=subject),0),
    'securities',NULLIF((SELECT count(*) FROM finance.exchange_positions p JOIN finance.accounts a ON a.id=p.account_id
      WHERE a.owner_principal_id=subject AND (p.quantity<>0 OR p.reserved<>0)),0),
    'exchange_orders',NULLIF((SELECT count(*) FROM finance.exchange_orders WHERE actor_id=subject AND status IN ('open','partially_filled')),0),
    'atm_operations',NULLIF((SELECT count(*) FROM finance.atm_operations WHERE user_id=subject AND status NOT IN ('COMPLETED','FAILED')),0),
    'payroll_items',NULLIF((SELECT count(*) FROM finance.payroll_items i JOIN finance.payroll_batches b ON b.id=i.batch_id
      WHERE (i.user_id=subject OR b.created_by=subject OR b.approved_by=subject) AND b.status NOT IN ('completed','cancelled')),0),
    'payment_requests',NULLIF((SELECT count(*) FROM finance.payment_requests WHERE (payer_id=subject OR creator_id=subject) AND status='pending'),0),
    'unsettled_idempotency',NULLIF((SELECT count(*) FROM finance.idempotency_records WHERE scope_id=subject AND state='processing'),0)
  ))
$$;

-- Only deterministic personal-field redactions are exceptions to immutability.
-- Numeric journals, identifiers, statuses, timestamps and ownership are immutable.
CREATE FUNCTION finance.privacy_redaction_allowed(tab text,oldrow jsonb,newrow jsonb) RETURNS boolean LANGUAGE plpgsql STABLE AS $$
DECLARE cols text[]; k text; subject uuid; eligible boolean;
BEGIN
  cols := CASE tab
    WHEN 'financial_operations' THEN ARRAY['reason','request_id','correlation_id']
    WHEN 'issuance_records' THEN ARRAY['authority_reference']
    WHEN 'profile_events' THEN ARRAY['metadata']
    WHEN 'atm_events' THEN ARRAY['details']
    WHEN 'exchange_issuances' THEN ARRAY['reason']
    WHEN 'payment_requests' THEN ARRAY['description','external_id','metadata']
    WHEN 'payroll_batches' THEN ARRAY['description']
    ELSE '{}'::text[] END;
  IF cardinality(cols)=0 OR (oldrow-cols) IS DISTINCT FROM (newrow-cols) THEN RETURN false; END IF;
  subject := COALESCE(oldrow->>'initiator_id',oldrow->>'actor_id',oldrow->>'creator_id',oldrow->>'created_by')::uuid;
  eligible := (oldrow->>'created_at')::timestamptz<clock_timestamp()-interval '365 days'
    OR EXISTS(SELECT 1 FROM finance.privacy_subjects s WHERE s.status='erasing' AND
      (s.user_id=subject OR oldrow::text LIKE ANY(SELECT '%'||v||'%' FROM unnest(finance.privacy_scope(s.user_id)) v)));
  IF NOT COALESCE(eligible,false) OR finance.privacy_row_has_hold(oldrow) THEN RETURN false; END IF;
  FOREACH k IN ARRAY cols LOOP
    IF oldrow->k IS DISTINCT FROM newrow->k AND newrow->k IS DISTINCT FROM '"[redacted]"'::jsonb
      AND newrow->k IS DISTINCT FROM '{}'::jsonb AND newrow->k IS DISTINCT FROM 'null'::jsonb THEN RETURN false; END IF;
  END LOOP;
  RETURN true;
END $$;

CREATE OR REPLACE FUNCTION finance.prevent_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='UPDATE' AND finance.privacy_redaction_allowed(TG_TABLE_NAME,to_jsonb(OLD),to_jsonb(NEW)) THEN RETURN NEW; END IF;
  RAISE EXCEPTION 'immutable financial history';
END $$;

CREATE OR REPLACE FUNCTION finance.payroll_batch_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='UPDATE' AND OLD.status IN ('completed','cancelled') AND
    finance.privacy_redaction_allowed(TG_TABLE_NAME,to_jsonb(OLD),to_jsonb(NEW)) THEN RETURN NEW; END IF;
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

CREATE FUNCTION finance.privacy_minimize(subject uuid DEFAULT NULL) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE changed integer:=0; n integer; cutoff timestamptz:=clock_timestamp()-interval '365 days';
BEGIN
  IF subject IS NOT NULL AND finance.privacy_blockers(subject)<>'{}'::jsonb THEN
    RETURN jsonb_build_object('blocked',finance.privacy_blockers(subject)); END IF;
  UPDATE finance.financial_operations o SET reason='[redacted]',request_id='[redacted]',correlation_id='[redacted]'
    WHERE (reason<>'[redacted]' OR request_id<>'[redacted]' OR correlation_id IS DISTINCT FROM '[redacted]')
      AND ((subject IS NULL AND created_at<cutoff) OR (subject IS NOT NULL AND o.id::text=ANY(finance.privacy_scope(subject))))
      AND NOT finance.privacy_row_has_hold(to_jsonb(o))
      AND NOT EXISTS(SELECT 1 FROM finance.holds h WHERE h.operation_id=o.id AND h.status='active');
  GET DIAGNOSTICS n=ROW_COUNT; changed:=changed+n;
  UPDATE finance.issuance_records i SET authority_reference='[redacted]'
    WHERE authority_reference<>'[redacted]' AND NOT finance.privacy_row_has_hold(to_jsonb(i))
    AND ((subject IS NULL AND created_at<cutoff) OR (subject IS NOT NULL AND i.operation_id::text=ANY(finance.privacy_scope(subject))));
  GET DIAGNOSTICS n=ROW_COUNT; changed:=changed+n;
  UPDATE finance.profile_events SET metadata='{}' WHERE metadata<>'{}' AND NOT finance.privacy_row_has_hold(to_jsonb(profile_events))
    AND ((subject IS NULL AND created_at<cutoff) OR (subject IS NOT NULL AND to_jsonb(profile_events)::text LIKE ANY(SELECT '%'||v||'%' FROM unnest(finance.privacy_scope(subject)) v)));
  GET DIAGNOSTICS n=ROW_COUNT; changed:=changed+n;
  UPDATE finance.exchange_issuances SET reason='[redacted]' WHERE reason<>'[redacted]' AND NOT finance.privacy_row_has_hold(to_jsonb(exchange_issuances))
    AND ((subject IS NULL AND created_at<cutoff) OR actor_id=subject);
  GET DIAGNOSTICS n=ROW_COUNT; changed:=changed+n;
  UPDATE finance.atm_events e SET details='{}' WHERE details<>'{}' AND NOT finance.privacy_row_has_hold(to_jsonb(e))
    AND ((subject IS NULL AND created_at<cutoff) OR (subject IS NOT NULL AND to_jsonb(e)::text LIKE ANY(SELECT '%'||v||'%' FROM unnest(finance.privacy_scope(subject)) v)))
    AND NOT EXISTS(SELECT 1 FROM finance.atm_operations o WHERE o.id=e.operation_id AND o.status NOT IN ('COMPLETED','FAILED'));
  GET DIAGNOSTICS n=ROW_COUNT; changed:=changed+n;
  UPDATE finance.atm_operations SET nickname='Deleted',player_uuid='00000000-0000-0000-0000-000000000000',error='',evidence='{}'
    WHERE status IN ('COMPLETED','FAILED') AND NOT finance.privacy_row_has_hold(to_jsonb(atm_operations))
    AND ((subject IS NULL AND created_at<cutoff) OR user_id=subject) AND nickname<>'Deleted';
  GET DIAGNOSTICS n=ROW_COUNT; changed:=changed+n;
  UPDATE finance.payroll_batches SET description='[redacted]' WHERE status IN ('completed','cancelled') AND description<>'[redacted]'
    AND NOT finance.privacy_row_has_hold(to_jsonb(payroll_batches)) AND ((subject IS NULL AND created_at<cutoff) OR created_by=subject);
  GET DIAGNOSTICS n=ROW_COUNT; changed:=changed+n;
  UPDATE finance.authorities SET reason='[redacted]' WHERE reason<>'[redacted]' AND NOT finance.privacy_row_has_hold(to_jsonb(authorities))
    AND ((subject IS NULL AND created_at<cutoff) OR principal_id=subject OR granted_by=subject);
  GET DIAGNOSTICS n=ROW_COUNT; changed:=changed+n;
  UPDATE finance.event_outbox SET payload='{}' WHERE payload<>'{}' AND acknowledged_at IS NOT NULL
    AND NOT finance.privacy_row_has_hold(to_jsonb(event_outbox)) AND ((subject IS NULL AND created_at<cutoff) OR principal_id=subject);
  GET DIAGNOSTICS n=ROW_COUNT; changed:=changed+n;
  UPDATE finance.payment_requests p SET description='[redacted]',external_id=NULL,metadata='{}'
    WHERE status<>'pending' AND (description<>'[redacted]' OR external_id IS NOT NULL OR metadata<>'{}')
    AND NOT finance.privacy_row_has_hold(to_jsonb(p))
    AND ((subject IS NULL AND created_at<cutoff) OR payer_id=subject OR creator_id=subject);
  GET DIAGNOSTICS n=ROW_COUNT; changed:=changed+n;
  UPDATE finance.atm_devices SET observations='{}',error='' WHERE last_seen_at<clock_timestamp()-interval '10 seconds' AND observations<>'{}';
  RETURN jsonb_build_object('minimized',changed,'ledger_rows_deleted',0,
    'structural_records','pseudonymous financial links retained for balances, assets and replay safety; legal basis required');
END $$;

    CREATE OR REPLACE FUNCTION finance.payment_request_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='UPDATE' AND OLD.status<>'pending' AND finance.privacy_redaction_allowed(TG_TABLE_NAME,to_jsonb(OLD),to_jsonb(NEW)) THEN RETURN NEW; END IF;
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

    CREATE OR REPLACE FUNCTION finance.payment_request_event() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE who uuid; merchant text; category text;
    BEGIN
      IF TG_OP='UPDATE' AND finance.privacy_redaction_allowed(TG_TABLE_NAME,to_jsonb(OLD),to_jsonb(NEW)) THEN RETURN NEW; END IF;
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

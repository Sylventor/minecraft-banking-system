#include "horizon/postgres_ledger.hpp"

#include <algorithm>
#include <array>
#include <pqxx/pqxx>
#include <string_view>

#include "horizon/business_internal.hpp"
#include "horizon/error.hpp"
#include "horizon/sql.hpp"

namespace horizon {
namespace {
[[noreturn]] void Reject(ErrorCode code, const std::string& message) { throw DomainError(code, message); }

void RequirePermission(pqxx::work& tx, const TransferCommand& cmd, const std::string& account,
                       const std::string& permission) {
  // Revocation UPDATE waits for this shared row lock until financial commit.
  const auto rows = sql::Exec(tx,
                              "SELECT id FROM finance.account_permissions WHERE account_id=$1::uuid "
                              "AND principal_id=$2::uuid AND permission=$3 AND revoked_at IS NULL FOR SHARE",
                              account, cmd.actor_id, permission);
  if (rows.empty()) Reject(ErrorCode::kDenied, "account permission is not granted");
}

void RequireAuthority(pqxx::work& tx, const TransferCommand& cmd, const std::string& permission) {
  const auto rows = sql::Exec(tx,
                              "SELECT principal_id FROM finance.authorities WHERE principal_id=$1::uuid "
                              "AND permission=$2 AND revoked_at IS NULL FOR SHARE",
                              cmd.actor_id, permission);
  if (rows.empty()) Reject(ErrorCode::kDenied, "financial authority is not granted");
  if (cmd.reason.empty()) Reject(ErrorCode::kInvalid, "a reason is required");
}

void Validate(const TransferCommand& cmd, bool with_amount = true) {
  if (cmd.actor_id.empty() || cmd.request_id.empty() || cmd.idempotency_key.size() < 16 ||
      cmd.idempotency_key.size() > 255 || cmd.fingerprint.size() != 64 ||
      std::count_if(cmd.reason.begin(), cmd.reason.end(), [](unsigned char ch) { return (ch & 0xc0U) != 0x80U; }) >
          500 ||
      cmd.source.size() > 32 || cmd.not_after.size() > 40)
    Reject(ErrorCode::kInvalid, "invalid operation context or idempotency data");
  if (with_amount && cmd.amount.cents() == 0) Reject(ErrorCode::kInvalid, "amount must be positive");
}

OperationResult ReadOperation(pqxx::transaction_base& tx, const std::string& id) {
  const auto rows = sql::Exec(tx,
                              "SELECT o.id::text,o.operation_type,o.status,COALESCE(o.source_account_id::text,''),"
                              "COALESCE(o.destination_account_id::text,''),o.amount::text,o.created_at::text,"
                              "COALESCE(o.completed_at::text,''),COALESCE(o.related_operation_id::text,''),"
                              "COALESCE(h.id::text,'') FROM finance.financial_operations o LEFT JOIN finance.holds h "
                              "ON h.operation_id=o.id WHERE o.id=$1::uuid",
                              id);
  if (rows.empty()) Reject(ErrorCode::kNotFound, "operation not found");
  const auto row = rows[0];
  return {row[0].c_str(), row[1].c_str(), row[2].c_str(),
          row[3].c_str(), row[4].c_str(), row[6].c_str(),
          row[7].c_str(), row[8].c_str(), Money::Parse(row[5].c_str()),
          row[9].c_str()};
}

bool Claim(pqxx::work& tx, const TransferCommand& cmd, OperationResult& existing) {
  // Request-outcome reads use the same lock, so they wait for in-flight commands.
  sql::Exec(tx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
            cmd.actor_id + ":" + cmd.operation_type + ":" + cmd.idempotency_key);
  // Bind the edge fingerprint to the actual command as a second independent check.
  auto digest = sql::One(tx,
                         "SELECT encode(sha256(convert_to(json_build_array($1::text,$2::text,$3::text,$4::text,"
                         "$5::text,$6::text,$7::text,$8::text)::text,'UTF8')),'hex')",
                         cmd.fingerprint, cmd.actor_id, cmd.operation_type, cmd.source_account_id,
                         cmd.destination_account_id, cmd.amount.ToString(), cmd.reason, cmd.authority_reference)[0]
                    .as<std::string>();
  // Keep the pre-2.1 fingerprint exactly unchanged when no deadline was supplied.
  if (!cmd.not_after.empty())
    digest = sql::One(tx, "SELECT encode(sha256(convert_to(json_build_array($1::text,$2::text)::text,'UTF8')),'hex')",
                      digest, cmd.not_after)[0]
                 .as<std::string>();
  const auto inserted = sql::Exec(
      tx,
      "INSERT INTO finance.idempotency_records(scope_id,operation_type,idempotency_key,request_fingerprint,state) "
      "VALUES($1::uuid,$2,$3,$4,'processing') ON CONFLICT(scope_id,operation_type,idempotency_key) "
      "DO NOTHING RETURNING id",
      cmd.actor_id, cmd.operation_type, cmd.idempotency_key, digest);
  if (!inserted.empty()) {
    if (!cmd.not_after.empty() &&
        !sql::One(tx, "SELECT clock_timestamp()<$1::timestamptz", cmd.not_after)[0].as<bool>())
      Reject(ErrorCode::kConflict, "transfer confirmation expired");
    return true;
  }
  const auto row = sql::One(tx,
                            "SELECT request_fingerprint,state,operation_id::text FROM finance.idempotency_records "
                            "WHERE scope_id=$1::uuid AND operation_type=$2 AND idempotency_key=$3 FOR UPDATE",
                            cmd.actor_id, cmd.operation_type, cmd.idempotency_key);
  if (row[0].as<std::string>() != digest) Reject(ErrorCode::kConflict, "idempotency payload mismatch");
  if (row[1].as<std::string>() != "completed" || row[2].is_null())
    Reject(ErrorCode::kConflict, "operation is still processing");
  existing = ReadOperation(tx, row[2].c_str());
  return false;
}

std::string NewId(pqxx::work& tx) { return sql::One(tx, "SELECT gen_random_uuid()::text")[0].as<std::string>(); }

void CheckLimit(pqxx::work& tx, const TransferCommand& cmd) {
  const auto row =
      sql::One(tx, "SELECT max_operation::text,max_issuance::text FROM finance.policy WHERE id=1 FOR SHARE");
  const auto maximum = Money::Parse(row[cmd.operation_type == "issue" ? 1 : 0].c_str());
  if (maximum < cmd.amount) Reject(ErrorCode::kLimit, "operation amount exceeds configured limit");
}

OperationResult Record(pqxx::work& tx, const TransferCommand& cmd, const std::string& related = {},
                       const std::string& hold_id = {}) {
  const auto id = NewId(tx);
  sql::Exec(
      tx,
      "INSERT INTO finance.financial_operations(id,operation_type,status,source_account_id,destination_account_id,"
      "amount,currency,initiator_id,request_id,correlation_id,reason,related_operation_id,completed_at,source) "
      "VALUES($1::uuid,$2,'completed',NULLIF($3,'')::uuid,NULLIF($4,'')::uuid,$5::numeric,'dB',"
      "$6::uuid,$7,$8,$9,NULLIF($10,'')::uuid,now(),$11)",
      id, cmd.operation_type, cmd.source_account_id, cmd.destination_account_id, cmd.amount.ToString(), cmd.actor_id,
      cmd.request_id, cmd.correlation_id, cmd.reason, related, cmd.source);
  if (cmd.operation_type != "hold" && cmd.operation_type != "release_hold") {
    const auto ledger_id = NewId(tx);
    sql::Exec(tx, "INSERT INTO finance.ledger_transactions(id,operation_id) VALUES($1::uuid,$2::uuid)", ledger_id, id);
    sql::Exec(tx,
              "INSERT INTO finance.ledger_entries(transaction_id,account_id,direction,amount,currency) "
              "VALUES($1::uuid,$2::uuid,'debit',$3::numeric,'dB'),($1::uuid,$4::uuid,'credit',$3::numeric,'dB')",
              ledger_id, cmd.source_account_id, cmd.amount.ToString(), cmd.destination_account_id);
  }
  if (cmd.operation_type == "issue") {
    sql::Exec(tx,
              "INSERT INTO finance.issuance_records(operation_id,target_account_id,amount,authority_reference) "
              "VALUES($1::uuid,$2::uuid,$3::numeric,$4)",
              id, cmd.destination_account_id, cmd.amount.ToString(), cmd.authority_reference);
  }
  if (cmd.operation_type == "hold") {
    sql::Exec(tx,
              "INSERT INTO finance.holds(id,account_id,operation_id,amount,status) "
              "VALUES($1::uuid,$2::uuid,$3::uuid,$4::numeric,'active')",
              hold_id, cmd.source_account_id, id, cmd.amount.ToString());
  }
  sql::Exec(tx,
            "UPDATE finance.idempotency_records SET state='completed',operation_id=$4::uuid,completed_at=now() "
            "WHERE scope_id=$1::uuid AND operation_type=$2 AND idempotency_key=$3",
            cmd.actor_id, cmd.operation_type, cmd.idempotency_key, id);
  return ReadOperation(tx, id);
}

void Move(pqxx::work& tx, const TransferCommand& cmd) {
  if (cmd.source_account_id == cmd.destination_account_id) Reject(ErrorCode::kInvalid, "accounts must differ");
  const auto rows = sql::Exec(tx,
                              "SELECT id::text,status,account_type,balance::text,held::text FROM finance.accounts "
                              "WHERE id IN($1::uuid,$2::uuid) ORDER BY id FOR UPDATE",
                              cmd.source_account_id, cmd.destination_account_id);
  if (rows.size() != 2) Reject(ErrorCode::kNotFound, "account not found");
  for (const auto& row : rows) {
    if (row[1].as<std::string>() != "active") Reject(ErrorCode::kConflict, "account is not active");
    const bool source = row[0].as<std::string>() == cmd.source_account_id;
    const bool system = row[2].as<std::string>() == "system";
    if (source && system) {
      if (cmd.operation_type != "issue") Reject(ErrorCode::kDenied, "system account is reserved for issuance");
      const auto raw = row[3].as<std::string>();
      const auto issued = Money::Parse(!raw.empty() && raw.front() == '-' ? raw.substr(1) : raw);
      static_cast<void>(issued + cmd.amount);
    } else {
      if (system) {
        // Only the fenced ATM state machine may return withdrawn digital cash
        // to the issuance clearing account. Ordinary transfers remain forbidden.
        if (cmd.source != "atm" || cmd.operation_type != "capture_hold")
          Reject(ErrorCode::kInvalid, "system account cannot receive user funds");
        continue;
      }
      const auto balance = Money::Parse(row[3].c_str());
      const auto held = Money::Parse(row[4].c_str());
      if (source && balance - held < cmd.amount) Reject(ErrorCode::kInsufficient, "insufficient available balance");
      if (!source) static_cast<void>(balance + cmd.amount);
    }
  }
  // Recheck the deadline after account locks: waiting for another transfer must
  // never turn an expired confirmation into a successful debit.
  if (!cmd.not_after.empty() && !sql::One(tx, "SELECT clock_timestamp()<$1::timestamptz", cmd.not_after)[0].as<bool>())
    Reject(ErrorCode::kConflict, "transfer confirmation expired");
  sql::Exec(tx, "UPDATE finance.accounts SET balance=balance-$2::numeric,updated_at=now() WHERE id=$1::uuid",
            cmd.source_account_id, cmd.amount.ToString());
  sql::Exec(tx, "UPDATE finance.accounts SET balance=balance+$2::numeric,updated_at=now() WHERE id=$1::uuid",
            cmd.destination_account_id, cmd.amount.ToString());
}
}  // namespace

PostgresLedger::PostgresLedger(std::string connection_string) : connection_string_(std::move(connection_string)) {}

OperationResult PostgresLedger::ExchangeTransfer(pqxx::work& tx, TransferCommand cmd) {
  cmd.operation_type = "transfer";
  cmd.source = "exchange";
  Validate(cmd);
  RequirePermission(tx, cmd, cmd.source_account_id, "transfer");
  OperationResult result;
  if (Claim(tx, cmd, result)) {
    CheckLimit(tx, cmd);
    Move(tx, cmd);
    result = Record(tx, cmd);
  }
  return result;
}

OperationResult PostgresLedger::AtmSettlement(pqxx::work& tx, TransferCommand cmd,
                                             const std::string& action, const std::string& hold_id) {
  if (cmd.source != "atm") Reject(ErrorCode::kDenied, "ATM context required");
  cmd.operation_type = action == "reserve" ? "hold" : action == "cancel" ? "release_hold" :
                       action == "withdraw" ? "capture_hold" : action == "deposit" ? "issue" :
                       action == "hpay" ? "payment" : "transfer";
  Validate(cmd);
  OperationResult result;
  if (!Claim(tx, cmd, result)) return result;
  CheckLimit(tx, cmd);
  if (action == "reserve") {
    RequirePermission(tx, cmd, cmd.source_account_id, "hold.create");
    const auto a = sql::One(tx,
        "SELECT status,balance::text,held::text FROM finance.accounts WHERE id=$1::uuid FOR UPDATE",
        cmd.source_account_id);
    if (a[0].as<std::string>() != "active") Reject(ErrorCode::kConflict, "account is not active");
    if (Money::Parse(a[1].c_str()) - Money::Parse(a[2].c_str()) < cmd.amount)
      Reject(ErrorCode::kInsufficient, "insufficient available balance");
    sql::Exec(tx, "UPDATE finance.accounts SET held=held+$2::numeric,updated_at=now() WHERE id=$1::uuid",
              cmd.source_account_id, cmd.amount.ToString());
    return Record(tx, cmd, {}, NewId(tx));
  }
  std::string related;
  if (action == "withdraw" || action == "cancel") {
    const auto h = sql::One(tx,
        "SELECT account_id::text,amount::text,status,operation_id::text FROM finance.holds WHERE id=$1::uuid FOR UPDATE",
        hold_id);
    if (h[0].as<std::string>() != cmd.source_account_id || Money::Parse(h[1].c_str()) != cmd.amount ||
        h[2].as<std::string>() != "active") Reject(ErrorCode::kConflict, "ATM reservation is not active");
    related = h[3].c_str();
    sql::Exec(tx, "UPDATE finance.accounts SET held=held-$2::numeric,updated_at=now() WHERE id=$1::uuid",
              cmd.source_account_id, cmd.amount.ToString());
    sql::Exec(tx, "UPDATE finance.holds SET status=$2,released_at=CASE WHEN $2='released' THEN now() END,"
                  "captured_at=CASE WHEN $2='captured' THEN now() END WHERE id=$1::uuid",
              hold_id, action == "cancel" ? "released" : "captured");
  }
  if (action == "transfer" || action == "hpay") RequirePermission(tx, cmd, cmd.source_account_id, "transfer");
  if (action != "cancel") Move(tx, cmd);
  return Record(tx, cmd, related);
}

void PostgresLedger::PayPayrollItem(const std::string& item_id, const std::string& actor) {
  pqxx::connection db{connection_string_};
  pqxx::work tx{db};
  const auto parent = sql::Exec(tx, "SELECT batch_id::text FROM finance.payroll_items WHERE id=$1::uuid", item_id);
  if (parent.empty()) Reject(ErrorCode::kNotFound, "payroll item not found");
  const auto batch = parent[0][0].as<std::string>();
  const auto b = sql::One(tx,
                          "SELECT "
                          "status,organization_id::text,source_account_id::text,COALESCE(approved_by::text,''),"
                          "executor_id::text,description "
                          "FROM finance.payroll_batches WHERE id=$1::uuid FOR UPDATE",
                          batch);
  const auto i = sql::One(tx,
                          "SELECT status,user_id::text,destination_account_id::text,amount::text FROM "
                          "finance.payroll_items WHERE id=$1::uuid FOR UPDATE",
                          item_id);
  if (i[0].as<std::string>() == "completed" || i[0].as<std::string>() == "failed") return;
  if (b[0].as<std::string>() != "processing" || b[4].as<std::string>() != actor)
    Reject(ErrorCode::kDenied, "persisted payroll executor required");
  business::Require(tx, actor, b[1].c_str(), "payroll.execute");
  business::Require(tx, b[3].c_str(), b[1].c_str(), "payroll.approve");
  business::RequireMandate(tx, batch, b[1].c_str());
  if (sql::Exec(tx,
                "SELECT id FROM finance.accounts WHERE id=$1::uuid AND owner_principal_id=$2::uuid AND "
                "account_type='personal' AND status='active'",
                i[2].c_str(), i[1].c_str())
          .empty())
    Reject(ErrorCode::kConflict, "payroll destination is invalid");
  TransferCommand cmd;
  cmd.actor_id = actor;
  cmd.source_account_id = b[2].c_str();
  cmd.destination_account_id = i[2].c_str();
  cmd.amount = Money::Parse(i[3].c_str());
  cmd.idempotency_key = "payroll-item:" + item_id;
  cmd.operation_type = "transfer";
  cmd.reason = b[5].c_str();
  cmd.source = "payroll";
  cmd.authority_reference = batch;
  cmd.request_id = cmd.idempotency_key;
  cmd.correlation_id = batch;
  cmd.fingerprint =
      sql::One(tx, "SELECT encode(sha256(convert_to($1,'UTF8')),'hex')", cmd.idempotency_key)[0].as<std::string>();
  Validate(cmd);
  RequirePermission(tx, cmd, cmd.source_account_id, "transfer");
  OperationResult operation;
  if (Claim(tx, cmd, operation)) {
    CheckLimit(tx, cmd);
    Move(tx, cmd);
    operation = Record(tx, cmd);
  }
  sql::Exec(tx,
            "UPDATE finance.payroll_items SET status='completed',operation_id=$2::uuid,paid_at=now(),error_code=NULL "
            "WHERE id=$1::uuid",
            item_id, operation.id);
  sql::Exec(tx,
            "INSERT INTO finance.operation_categories(organization_id,operation_id,category_key,changed_by) "
            "VALUES($1::uuid,$2::uuid,'payroll',$3::uuid) ON CONFLICT DO NOTHING",
            b[1].c_str(), operation.id, actor);
  sql::Exec(tx,
            "INSERT INTO finance.event_outbox(event_key,principal_id,category,payload) SELECT "
            "'salary:'||$1,$2::uuid,'incoming',"
            "jsonb_build_object('type','payroll.salary','operation_id',$3::text,'amount',$4::text,'currency','DB','"
            "direction','incoming',"
            "'organization',name,'description',$5::text,'date',clock_timestamp(),'status','completed') FROM "
            "finance.organizations WHERE id=$6::uuid",
            item_id, i[1].c_str(), operation.id, cmd.amount.ToString(), cmd.reason, b[1].c_str());
  business::Audit(tx, actor, "item.completed", item_id);
  tx.commit();
}

OperationResult PostgresLedger::PayRequest(TransferCommand cmd, const std::string& request_id) {
  pqxx::connection db{connection_string_};
  pqxx::work tx{db};
  sql::Exec(tx, "SELECT set_config('horizon.actor_id',$1,true)", cmd.actor_id);
  const auto rows =
      sql::Exec(tx,
                "SELECT payer_id::text,status,recipient_account_id::text,amount::text,description,expires_at::text,"
                "COALESCE(pay_key,''),COALESCE(source_account_id::text,''),COALESCE(transaction_id::text,'') "
                "FROM finance.payment_requests WHERE id=$1::uuid FOR UPDATE",
                request_id);
  if (rows.empty()) Reject(ErrorCode::kNotFound, "payment request not found");
  const auto row = rows[0];
  if (row[0].as<std::string>() != cmd.actor_id) Reject(ErrorCode::kDenied, "only the payer may pay");
  if (row[1].as<std::string>() == "paid") {
    if (row[6].as<std::string>() != cmd.idempotency_key || row[7].as<std::string>() != cmd.source_account_id)
      Reject(ErrorCode::kConflict, "payment request is already settled");
    auto result = ReadOperation(tx, row[8].c_str());
    tx.commit();
    return result;
  }
  if (row[1].as<std::string>() != "pending") Reject(ErrorCode::kConflict, "payment request is final");
  if (!sql::One(tx, "SELECT clock_timestamp()<$1::timestamptz", row[5].c_str())[0].as<bool>()) {
    sql::Exec(tx, "UPDATE finance.payment_requests SET status='expired' WHERE id=$1::uuid", request_id);
    tx.commit();
    Reject(ErrorCode::kConflict, "payment request expired");
  }
  if (sql::Exec(tx,
                "SELECT id FROM finance.accounts WHERE id=$1::uuid AND owner_principal_id=$2::uuid "
                "AND account_type='personal'",
                cmd.source_account_id, cmd.actor_id)
          .empty())
    Reject(ErrorCode::kDenied, "payer must use an owned personal account");
  cmd.destination_account_id = row[2].c_str();
  cmd.amount = Money::Parse(row[3].c_str());
  cmd.reason = row[4].c_str();
  cmd.not_after = row[5].c_str();
  cmd.operation_type = "payment";
  Validate(cmd);
  RequirePermission(tx, cmd, cmd.source_account_id, "payment.create");
  const auto pay_key = cmd.idempotency_key;
  cmd.idempotency_key = "payment-request:" + request_id;
  OperationResult result;
  if (Claim(tx, cmd, result)) {
    CheckLimit(tx, cmd);
    sql::Exec(tx,
              "INSERT INTO finance.profile_events(actor_id,action,resource_id) "
              "VALUES($1::uuid,'payment_request.confirm',$2::uuid)",
              cmd.actor_id, request_id);
    Move(tx, cmd);
    result = Record(tx, cmd);
  }
  sql::Exec(tx,
            "UPDATE finance.payment_requests SET status='paid',paid_at=clock_timestamp(),transaction_id=$2::uuid,"
            "source_account_id=$3::uuid,pay_key=$4 WHERE id=$1::uuid",
            request_id, result.id, cmd.source_account_id, pay_key);
  tx.commit();
  return result;
}

OperationResult PostgresLedger::Transfer(const TransferCommand& cmd) {
  Validate(cmd);
  pqxx::connection db{connection_string_};
  pqxx::work tx{db};
  OperationResult existing;
  if (cmd.operation_type == "issue") {
    RequireAuthority(tx, cmd, "issue.create");
    RequirePermission(tx, cmd, cmd.destination_account_id, "issue.create");
    if (cmd.authority_reference.empty()) Reject(ErrorCode::kInvalid, "authority reference is required");
    const auto source =
        sql::One(tx, "SELECT account_type FROM finance.accounts WHERE id=$1::uuid", cmd.source_account_id);
    if (source[0].as<std::string>() != "system") Reject(ErrorCode::kInvalid, "issuance requires system source");
  } else if (cmd.operation_type == "transfer" || cmd.operation_type == "payment") {
    RequirePermission(tx, cmd, cmd.source_account_id, cmd.operation_type == "transfer" ? "transfer" : "payment.create");
  } else
    Reject(ErrorCode::kInvalid, "unsupported transfer type");
  if (!Claim(tx, cmd, existing)) {
    tx.commit();
    return existing;
  }
  CheckLimit(tx, cmd);
  Move(tx, cmd);
  auto result = Record(tx, cmd);
  tx.commit();
  return result;
}

OperationResult PostgresLedger::CreateHold(const TransferCommand& cmd) {
  Validate(cmd);
  if (cmd.operation_type != "hold") Reject(ErrorCode::kInvalid, "invalid hold type");
  pqxx::connection db{connection_string_};
  pqxx::work tx{db};
  OperationResult existing;
  RequirePermission(tx, cmd, cmd.source_account_id, "hold.create");
  if (!Claim(tx, cmd, existing)) {
    tx.commit();
    return existing;
  }
  CheckLimit(tx, cmd);
  const auto rows = sql::Exec(
      tx, "SELECT status,balance::text,held::text,account_type FROM finance.accounts WHERE id=$1::uuid FOR UPDATE",
      cmd.source_account_id);
  if (rows.empty()) Reject(ErrorCode::kNotFound, "account not found");
  const auto row = rows[0];
  if (row[0].as<std::string>() != "active" || row[3].as<std::string>() == "system")
    Reject(ErrorCode::kConflict, "account does not allow holds");
  if (Money::Parse(row[1].c_str()) - Money::Parse(row[2].c_str()) < cmd.amount)
    Reject(ErrorCode::kInsufficient, "insufficient available balance");
  sql::Exec(tx, "UPDATE finance.accounts SET held=held+$2::numeric,updated_at=now() WHERE id=$1::uuid",
            cmd.source_account_id, cmd.amount.ToString());
  auto result = Record(tx, cmd, {}, NewId(tx));
  tx.commit();
  return result;
}

OperationResult PostgresLedger::Refund(const TransferCommand& cmd, const std::string& original_id) {
  Validate(cmd);
  if (original_id.empty() || cmd.operation_type != "refund") Reject(ErrorCode::kInvalid, "invalid refund");
  pqxx::connection db{connection_string_};
  pqxx::work tx{db};
  OperationResult existing;
  RequireAuthority(tx, cmd, "refund.create");
  const auto originals =
      sql::Exec(tx,
                "SELECT source_account_id::text,destination_account_id::text,amount::text,operation_type,status "
                "FROM finance.financial_operations WHERE id=$1::uuid FOR UPDATE",
                original_id);
  if (originals.empty()) Reject(ErrorCode::kNotFound, "original operation not found");
  const auto original = originals[0];
  if (original[4].as<std::string>() != "completed" ||
      (original[3].as<std::string>() != "payment" && original[3].as<std::string>() != "transfer"))
    Reject(ErrorCode::kInvalid, "operation is not refundable");
  TransferCommand reverse = cmd;
  reverse.source_account_id = original[1].c_str();
  reverse.destination_account_id = original[0].c_str();
  if (cmd.source_account_id != reverse.source_account_id)
    Reject(ErrorCode::kInvalid, "refund source must be original recipient");
  RequirePermission(tx, reverse, reverse.source_account_id, "refund.create");
  if (!Claim(tx, reverse, existing)) {
    tx.commit();
    return existing;
  }
  // Locking the immutable original above serializes the aggregate check.
  const auto refunded =
      sql::One(tx,
               "SELECT COALESCE(SUM(amount),0)::numeric(20,2)::text FROM finance.financial_operations "
               "WHERE related_operation_id=$1::uuid AND operation_type='refund' AND status='completed'",
               original_id);
  if (Money::Parse(original[2].c_str()) - Money::Parse(refunded[0].c_str()) < cmd.amount)
    Reject(ErrorCode::kConflict, "refund exceeds remaining amount");
  CheckLimit(tx, reverse);
  Move(tx, reverse);
  auto result = Record(tx, reverse, original_id);
  tx.commit();
  return result;
}

OperationResult PostgresLedger::ChangeHold(const TransferCommand& cmd, const std::string& hold_id, bool capture) {
  Validate(cmd, false);
  if (hold_id.empty() || cmd.operation_type != (capture ? "capture_hold" : "release_hold"))
    Reject(ErrorCode::kInvalid, "invalid hold action");
  pqxx::connection db{connection_string_};
  pqxx::work tx{db};
  OperationResult existing;
  const auto holds = sql::Exec(
      tx,
      "SELECT account_id::text,amount::text,status,operation_id::text FROM finance.holds WHERE id=$1::uuid FOR UPDATE",
      hold_id);
  if (holds.empty()) Reject(ErrorCode::kNotFound, "hold not found");
  const auto hold = holds[0];
  TransferCommand resolved = cmd;
  resolved.source_account_id = hold[0].c_str();
  resolved.amount = Money::Parse(hold[1].c_str());
  if (!capture) resolved.destination_account_id.clear();
  RequirePermission(tx, resolved, resolved.source_account_id, "hold.create");
  if (!Claim(tx, resolved, existing)) {
    tx.commit();
    return existing;
  }
  if (hold[2].as<std::string>() != "active") Reject(ErrorCode::kConflict, "hold is not active");
  if (capture &&
      (resolved.destination_account_id.empty() || resolved.destination_account_id == resolved.source_account_id))
    Reject(ErrorCode::kInvalid, "capture requires a different destination");
  const auto accounts = sql::Exec(
      tx, "SELECT id,status FROM finance.accounts WHERE id=$1::uuid OR id=NULLIF($2,'')::uuid ORDER BY id FOR UPDATE",
      resolved.source_account_id, resolved.destination_account_id);
  if (accounts.size() != (capture ? 2 : 1)) Reject(ErrorCode::kNotFound, "account not found");
  for (const auto& account : accounts) {
    if (account[1].as<std::string>() == "closed" || (capture && account[1].as<std::string>() != "active"))
      Reject(ErrorCode::kConflict, "account state does not permit hold action");
  }
  sql::Exec(tx, "UPDATE finance.accounts SET held=held-$2::numeric,updated_at=now() WHERE id=$1::uuid",
            resolved.source_account_id, resolved.amount.ToString());
  if (capture) Move(tx, resolved);
  sql::Exec(tx,
            "UPDATE finance.holds SET status=$2,released_at=CASE WHEN $2='released' THEN now() ELSE NULL END,"
            "captured_at=CASE WHEN $2='captured' THEN now() ELSE NULL END WHERE id=$1::uuid",
            hold_id, capture ? "captured" : "released");
  auto result = Record(tx, resolved, hold[3].c_str());
  tx.commit();
  return result;
}
}  // namespace horizon

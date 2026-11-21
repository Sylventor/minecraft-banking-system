#include "horizon/business.hpp"

#include <algorithm>
#include <vector>

#include "horizon/business_internal.hpp"
#include "horizon/postgres_ledger.hpp"

namespace horizon {
namespace {
using namespace business;
using Request = banking::v1::BusinessRequest;
std::string Schedule(pqxx::transaction_base& tx, const std::string& id) {
  return sql::
      One(tx,
          "SELECT (to_jsonb(s)||jsonb_build_object('max_amount',s.max_amount::text,'items',COALESCE((SELECT jsonb_agg("
          "to_jsonb(i)||jsonb_build_object('amount',i.amount::text) ORDER BY i.employee_id) FROM "
          "finance.payroll_schedule_items i "
          "WHERE i.schedule_id=s.id),'[]'::jsonb)))::text FROM finance.payroll_schedules s WHERE id=$1::uuid",
          id)[0]
          .as<std::string>();
}
void Source(pqxx::transaction_base& tx, const std::string& org, const std::string& account) {
  Id(account);
  if (sql::Exec(tx,
                "SELECT id FROM finance.accounts WHERE id=$1::uuid AND organization_id=$2::uuid AND "
                "account_type='organization' AND status='active'",
                account, org)
          .empty())
    throw DomainError(ErrorCode::kConflict, "active organization source required");
}
pqxx::result Snapshot(pqxx::transaction_base& tx, const std::string& org, const std::string& data,
                      const std::string& actor) {
  const auto items = sql::Exec(tx, R"SQL(SELECT e.id::text,e.user_id::text,e.account_id::text,
   COALESCE(x->>'amount',e.salary::text),e.revision,e.salary::text FROM jsonb_array_elements($2::jsonb->'items') x
   JOIN finance.payroll_employees e ON e.id=(x->>'employee_id')::uuid AND e.organization_id=$1::uuid
   JOIN finance.accounts a ON a.id=e.account_id AND a.owner_principal_id=e.user_id AND a.account_type='personal'
   WHERE e.active AND a.status='active' ORDER BY e.id FOR SHARE OF e)SQL",
                               org, data);
  const auto n = sql::One(tx, "SELECT jsonb_array_length($1::jsonb->'items')", data)[0].as<std::size_t>();
  if (n == 0 || n > 100 || items.size() != n)
    throw std::invalid_argument("select 1 to 100 active employees with valid personal accounts");
  std::vector<std::string> seen;
  for (const auto& item : items) {
    const auto id = item[0].as<std::string>();
    if (std::find(seen.begin(), seen.end(), id) != seen.end()) throw std::invalid_argument("duplicate employee");
    seen.push_back(id);
    Money::Parse(item[3].c_str());
    if (item[3].as<std::string>() != item[5].as<std::string>()) {
      Require(tx, actor, org, "payroll.salaries.manage");
      if (item[1].as<std::string>() == actor) throw DomainError(ErrorCode::kDenied, "cannot set own payroll amount");
    }
  }
  return items;
}
std::string CreateBatch(pqxx::transaction_base& tx, const std::string& org, const std::string& actor,
                        const std::string& data, const std::string& schedule = "", const std::string& occurrence = "") {
  const auto source = Field(tx, data, "source_account_id");
  Source(tx, org, source);
  const auto key = Field(tx, data, "creation_key");
  if (key.size() < 16 || key.size() > 255) throw std::invalid_argument("invalid batch idempotency key");
  const auto fingerprint =
      sql::One(tx, "SELECT encode(sha256(convert_to($1::jsonb::text,'UTF8')),'hex')", data)[0].as<std::string>();
  const auto old = sql::Exec(
      tx, "SELECT id::text,fingerprint FROM finance.payroll_batches WHERE organization_id=$1::uuid AND creation_key=$2",
      org, key);
  if (!old.empty()) {
    if (old[0][1].as<std::string>() != fingerprint)
      throw DomainError(ErrorCode::kConflict, "batch idempotency payload mismatch");
    return old[0][0].as<std::string>();
  }
  const auto items = Snapshot(tx, org, data, actor);
  Money total;
  for (const auto& row : items) total = total + Money::Parse(row[3].c_str());
  const auto id = sql::One(tx,
                           "INSERT INTO "
                           "finance.payroll_batches(organization_id,source_account_id,description,total,created_by,"
                           "creation_key,fingerprint,schedule_id,scheduled_for,schedule_revision) "
                           "VALUES($1::uuid,$2::uuid,$3,$4::numeric,$5::uuid,$6,$7,NULLIF($8,'')::uuid,NULLIF($9,'')::"
                           "timestamptz,(SELECT revision FROM finance.payroll_schedules WHERE id=NULLIF($8,'')::uuid)) "
                           "RETURNING id::text",
                           org, source, Field(tx, data, "description", "Зарплата"), total.ToString(), actor, key,
                           fingerprint, schedule, occurrence)[0]
                      .as<std::string>();
  for (const auto& row : items)
    sql::Exec(tx,
              "INSERT INTO finance.payroll_items(batch_id,employee_id,user_id,destination_account_id,amount) "
              "VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::numeric)",
              id, row[0].c_str(), row[1].c_str(), row[2].c_str(), row[3].c_str());
  Audit(tx, actor, "batch.create", id);
  return id;
}
void Approve(pqxx::transaction_base& tx, const std::string& org, const std::string& batch, const std::string& actor) {
  Require(tx, actor, org, "payroll.approve");
  if (!IsOwner(tx, actor, org) &&
      sql::One(tx,
               "SELECT created_by=$2::uuid OR EXISTS(SELECT 1 FROM finance.payroll_items WHERE batch_id=$1::uuid AND "
               "user_id=$2::uuid) "
               "FROM finance.payroll_batches WHERE id=$1::uuid",
               batch, actor)[0]
          .as<bool>())
    throw DomainError(ErrorCode::kDenied, "approval requires another authorized person who is not a recipient");
  sql::Exec(tx, "UPDATE finance.payroll_batches SET approved_by=$2::uuid,approved_at=now() WHERE id=$1::uuid", batch,
            actor);
  Audit(tx, actor, "batch.approve", batch);
}
void Begin(pqxx::transaction_base& tx, const std::string& batch, const std::string& org, const std::string& actor,
           bool retry) {
  Require(tx, actor, org, "payroll.execute");
  const auto b = sql::One(
      tx,
      "SELECT status,source_account_id::text,COALESCE(approved_by::text,''),COALESCE(executor_id::text,'') FROM "
      "finance.payroll_batches WHERE id=$1::uuid FOR UPDATE",
      batch);
  if (b[0].as<std::string>() == "completed") return;
  if (b[0].as<std::string>() == "processing") {
    if (b[3].as<std::string>() != actor)
      throw DomainError(ErrorCode::kConflict, "another executor is already processing this batch");
    return;
  }
  RequireMandate(tx, batch, org);
  if ((!retry && b[0].as<std::string>() != "pending_approval") ||
      (retry && b[0].as<std::string>() != "failed" && b[0].as<std::string>() != "partially_completed") ||
      b[2].as<std::string>().empty())
    throw DomainError(ErrorCode::kConflict, "approved batch required");
  Require(tx, b[2].c_str(), org, "payroll.approve");
  if (sql::Exec(tx,
                "SELECT id FROM finance.account_permissions WHERE account_id=$1::uuid AND principal_id=$2::uuid "
                "AND permission='transfer' AND revoked_at IS NULL FOR SHARE",
                b[1].c_str(), actor)
          .empty())
    throw DomainError(ErrorCode::kDenied, "source transfer permission is not granted");
  const auto balance = sql::One(
      tx, "SELECT balance::text,held::text,status FROM finance.accounts WHERE id=$1::uuid FOR UPDATE", b[1].c_str());
  const auto remaining = sql::One(tx,
                                  "SELECT COALESCE(sum(amount),0)::numeric(18,2)::text FROM finance.payroll_items "
                                  "WHERE batch_id=$1::uuid AND status<>'completed'",
                                  batch)[0]
                             .as<std::string>();
  if (balance[2].as<std::string>() != "active" ||
      Money::Parse(balance[0].c_str()) - Money::Parse(balance[1].c_str()) < Money::Parse(remaining))
    throw DomainError(ErrorCode::kInsufficient, "insufficient balance for complete remaining payroll");
  if (sql::One(
          tx,
          "SELECT EXISTS(SELECT 1 FROM finance.payroll_items i CROSS JOIN finance.policy p WHERE i.batch_id=$1::uuid "
          "AND i.status<>'completed' AND i.amount>p.max_operation)",
          batch)[0]
          .as<bool>())
    throw DomainError(ErrorCode::kLimit, "payroll item exceeds financial policy");
  if (retry)
    sql::Exec(
        tx,
        "UPDATE finance.payroll_items SET status='pending',error_code=NULL WHERE batch_id=$1::uuid AND status='failed'",
        batch);
  sql::Exec(
      tx,
      "UPDATE finance.payroll_batches SET status='processing',executor_id=$2::uuid,completed_at=NULL WHERE id=$1::uuid",
      batch, actor);
  Audit(tx, actor, retry ? "batch.retry" : "batch.execute", batch);
}
std::string Page(pqxx::transaction_base& tx, const pqxx::result& rows, int limit, const std::string& type) {
  std::string out = "{\"items\":[";
  int n = 0;
  for (const auto& row : rows) {
    if (n == limit) break;
    if (n++) out += ',';
    out += type == "batch"      ? Batch(tx, row[0].c_str())
           : type == "schedule" ? Schedule(tx, row[0].c_str())
                                : row[1].as<std::string>();
  }
  out += "],\"next_cursor\":";
  if (rows.size() > static_cast<pqxx::result::size_type>(limit)) {
    out += '"';
    out += rows[static_cast<pqxx::result::size_type>(limit - 1)][0].c_str();
    out += '"';
  } else
    out += "null";
  out += '}';
  return out;
}
}  // namespace

void Business::Process(const std::string& database, const std::string& batch, const std::string& actor) {
  std::vector<std::string> items;
  {
    pqxx::connection db{database};
    pqxx::read_transaction tx{db};
    for (const auto& row :
         sql::Exec(tx,
                   "SELECT i.id::text FROM finance.payroll_items i JOIN finance.payroll_batches b ON b.id=i.batch_id "
                   "WHERE b.id=$1::uuid AND b.status='processing' AND i.status='pending' ORDER BY i.id",
                   batch))
      items.push_back(row[0].as<std::string>());
  }
  for (const auto& id : items) {
    try {
      PostgresLedger(database).PayPayrollItem(id, actor);
    } catch (const DomainError& error) {
      pqxx::connection db{database};
      pqxx::work tx{db};
      const auto code = error.code() == ErrorCode::kInsufficient ? "insufficient_funds"
                        : error.code() == ErrorCode::kDenied     ? "permission_revoked"
                        : error.code() == ErrorCode::kLimit      ? "policy_limit"
                                                                 : "account_unavailable";
      sql::Exec(tx,
                "UPDATE finance.payroll_items SET status='failed',error_code=$2 WHERE id=$1::uuid AND status='pending'",
                id, code);
      business::Audit(tx, actor, "item.failed", id);
      tx.commit();
    }
  }
  pqxx::connection db{database};
  pqxx::work tx{db};
  const auto b = sql::One(
      tx, "SELECT status,organization_id::text FROM finance.payroll_batches WHERE id=$1::uuid FOR UPDATE", batch);
  if (b[0].as<std::string>() != "processing") return;
  const auto counts =
      sql::One(tx,
               "SELECT count(*) FILTER(WHERE status='pending'),count(*) FILTER(WHERE status='completed'),count(*) "
               "FILTER(WHERE status='failed') FROM finance.payroll_items WHERE batch_id=$1::uuid",
               batch);
  if (counts[0].as<int>() != 0) return;
  const auto state = counts[2].as<int>() == 0   ? "completed"
                     : counts[1].as<int>() == 0 ? "failed"
                                                : "partially_completed";
  sql::Exec(tx, "UPDATE finance.payroll_batches SET status=$2,completed_at=now() WHERE id=$1::uuid", batch, state);
  business::Audit(tx, actor, std::string("batch.") + state, batch);
  business::Notice(tx, b[1].c_str(), state, batch);
  if (counts[2].as<int>() != 0) {
    sql::Exec(
        tx,
        "UPDATE finance.payroll_schedules SET status='paused',last_error='batch_failed' WHERE id IN "
        "(SELECT schedule_id FROM finance.payroll_batches WHERE id=$1::uuid) AND mode='automatic' AND status='active'",
        batch);
  }
  tx.commit();
}

std::string Business::Execute(const std::string& database, const Request& r) {
  using namespace business;
  const auto actor = r.context().actor_id(), org = r.organization_id(), action = r.action(), id = r.target_id();
  if (action.rfind("analytics.", 0) == 0) return Analytics(database, r);
  const bool worker = r.context().source() == "payroll-worker";
  if (action == "worker.jobs") {
    if (!worker) throw DomainError(ErrorCode::kDenied, "worker context required");
    pqxx::connection db{database};
    pqxx::read_transaction tx{db};
    return sql::
        One(tx,
            "SELECT jsonb_build_object('schedules',COALESCE((SELECT "
            "jsonb_agg(to_jsonb(s)||jsonb_build_object('local_time',s.local_time::text,'max_amount',s.max_"
            "amount::text)) "
            "FROM (SELECT * FROM finance.payroll_schedules WHERE status='active' AND "
            "next_run_at<=clock_timestamp() ORDER BY next_run_at LIMIT 20) s),'[]'::jsonb),"
            "'batches',COALESCE((SELECT "
            "jsonb_agg(jsonb_build_object('id',id,'organization_id',organization_id,'actor',actor,'action',action)) "
            "FROM "
            "(SELECT b.id,b.organization_id,CASE WHEN b.status='processing' THEN b.executor_id ELSE b.approved_by END "
            "actor,"
            "CASE WHEN b.status='processing' THEN 'batch.resume' ELSE 'batch.execute' END action FROM "
            "finance.payroll_batches b "
            "LEFT JOIN finance.payroll_schedules s ON s.id=b.schedule_id WHERE b.status='processing' OR "
            "(b.status='pending_approval' AND b.approved_by IS NOT NULL AND s.mode='automatic' AND s.status='active' "
            "AND s.revision=b.schedule_revision) ORDER BY b.created_at LIMIT 20) b),'[]'::jsonb))::text")[0]
            .as<std::string>();
  }
  Id(org);
  if (!id.empty()) Id(id);
  const auto data = r.payload().empty() ? "{}" : r.payload();
  if (data.size() > 131072) throw std::invalid_argument("business payload too large");
  pqxx::connection db{database};
  pqxx::work tx{db};
  sql::Exec(tx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "business:" + org);
  if (action == "worker.schedule_error" || action == "worker.batch_error") {
    if (!worker) throw DomainError(ErrorCode::kDenied, "worker context required");
    const auto target = action == "worker.batch_error"
                            ? sql::One(tx,
                                       "SELECT COALESCE(schedule_id::text,'') FROM finance.payroll_batches WHERE "
                                       "id=$1::uuid AND organization_id=$2::uuid",
                                       id, org)[0]
                                  .as<std::string>()
                            : id;
    if (!target.empty()) {
      sql::Exec(tx,
                "UPDATE finance.payroll_schedules SET status='paused',last_error=$3 WHERE id=$1::uuid AND "
                "organization_id=$2::uuid AND status<>'disabled'",
                target, org, Field(tx, data, "reason", "execution_error"));
      Notice(tx, org, "schedule_paused", target);
      Audit(tx, actor, "schedule.error", target);
    }
    tx.commit();
    return "{}";
  }
  if (action == "permissions") {
    const auto p = sql::Exec(tx,
                             "SELECT array_to_json(permissions)::text FROM finance.organization_members WHERE "
                             "organization_id=$1::uuid AND principal_id=$2::uuid AND revoked_at IS NULL",
                             org, actor);
    if (p.empty()) throw DomainError(ErrorCode::kDenied, "organization membership required");
    return std::string("{\"permissions\":") + p[0][0].c_str() + "}";
  }
  if (action == "category.create" || action == "category.assign") {
    Require(tx, actor, org, "analytics.categorize");
    if (action == "category.create") {
      const auto category =
          sql::One(tx,
                   "INSERT INTO finance.business_categories(organization_id,key,name) "
                   "VALUES($1::uuid,'custom-'||gen_random_uuid(),$2) RETURNING to_jsonb(business_categories)::text",
                   org, Field(tx, data, "name"))[0]
              .as<std::string>();
      Audit(tx, actor, "category.create", org);
      tx.commit();
      return category;
    }
    if (!sql::One(tx,
                  "SELECT EXISTS(SELECT 1 FROM finance.financial_operations o JOIN finance.accounts a ON a.id IN "
                  "(o.source_account_id,o.destination_account_id) "
                  "WHERE o.id=$1::uuid AND a.organization_id=$2::uuid AND EXISTS(SELECT 1 FROM "
                  "finance.account_permissions p WHERE p.account_id=a.id AND p.principal_id=$3::uuid AND "
                  "p.permission='history.read' AND p.revoked_at IS NULL))",
                  id, org, actor)[0]
             .as<bool>())
      throw DomainError(ErrorCode::kNotFound, "organization operation not found");
    if (sql::One(tx, "SELECT EXISTS(SELECT 1 FROM finance.payroll_items WHERE operation_id=$1::uuid)", id)[0]
            .as<bool>())
      throw DomainError(ErrorCode::kDenied, "payroll classification is automatic");
    sql::Exec(tx,
              "INSERT INTO finance.operation_categories(organization_id,operation_id,category_key,changed_by) "
              "VALUES($1::uuid,$2::uuid,$3,$4::uuid) ON CONFLICT(organization_id,operation_id) DO UPDATE SET "
              "category_key=EXCLUDED.category_key,changed_by=EXCLUDED.changed_by,updated_at=now()",
              org, id, Field(tx, data, "category"), actor);
    Audit(tx, actor, "category.assign", id, data);
    tx.commit();
    return "{}";
  }
  if (action == "payroll.overview") {
    Require(tx, actor, org, "payroll.read");
    return sql::One(tx, R"SQL(SELECT jsonb_build_object(
      'employees',(SELECT count(*) FROM finance.payroll_employees WHERE organization_id=$1::uuid AND active),
      'paid_last30',(SELECT COALESCE(sum(i.amount),0)::numeric(38,2)::text FROM finance.payroll_items i JOIN finance.payroll_batches b ON b.id=i.batch_id
        WHERE b.organization_id=$1::uuid AND i.status='completed' AND i.paid_at>=clock_timestamp()-interval '30 days'),
      'last_batch',(SELECT jsonb_build_object('id',id,'status',status,'total',total::text,'created_at',created_at)
        FROM finance.payroll_batches WHERE organization_id=$1::uuid ORDER BY created_at DESC,id DESC LIMIT 1),
      'next_schedule',(SELECT jsonb_build_object('id',s.id,'name',s.name,'next_run_at',s.next_run_at,'mode',s.mode,
        'total',(SELECT COALESCE(sum(amount),0)::numeric(38,2)::text FROM finance.payroll_schedule_items WHERE schedule_id=s.id))
        FROM finance.payroll_schedules s WHERE organization_id=$1::uuid AND status='active' ORDER BY next_run_at,id LIMIT 1))::text)SQL",
                    org)[0]
        .as<std::string>();
  }
  if (action == "payroll.history") {
    Require(tx, actor, org, "payroll.read");
    const auto limit = std::clamp(std::stoi(Field(tx, data, "limit", "25")), 1, 100);
    const auto cursor = Field(tx, data, "cursor");
    if (!cursor.empty()) Id(cursor);
    const auto rows = sql::Exec(tx,
                                "SELECT "
                                "i.id::text,(to_jsonb(i)||jsonb_build_object('amount',i.amount::text,'description',b."
                                "description,'created_at',b.created_at))::text "
                                "FROM finance.payroll_items i JOIN finance.payroll_batches b ON b.id=i.batch_id WHERE "
                                "b.organization_id=$1::uuid "
                                "AND ($2='' OR i.id>NULLIF($2,'')::uuid) ORDER BY i.id LIMIT $3",
                                org, cursor, limit + 1);
    return Page(tx, rows, limit, "employee");
  }
  if (action.rfind("employee.", 0) == 0) {
    Require(tx, actor, org, action == "employee.list" ? "payroll.read" : "payroll.employees.manage");
    if (action == "employee.list") {
      const auto limit = std::clamp(std::stoi(Field(tx, data, "limit", "25")), 1, 100);
      const auto active = Field(tx, data, "active", "all");
      const auto cursor = Field(tx, data, "cursor");
      if (!cursor.empty()) Id(cursor);
      const auto q = Field(tx, data, "q");
      const auto rows = sql::Exec(
          tx,
          "SELECT id::text,(to_jsonb(e)||jsonb_build_object('salary',salary::text))::text FROM "
          "finance.payroll_employees e "
          "WHERE organization_id=$1::uuid AND ($2='' OR id>NULLIF($2,'')::uuid) AND ($3='all' OR active=($3='true')) "
          "AND ($4='' OR position(lower($4) in lower(position||' '||user_id::text))>0 OR user_id IN (SELECT "
          "value::uuid FROM jsonb_array_elements_text(COALESCE($6::jsonb->'user_ids','[]')))) ORDER BY id LIMIT $5",
          org, cursor, active, q, limit + 1, data);
      return Page(tx, rows, limit, "employee");
    }
    if (action == "employee.create") {
      Require(tx, actor, org, "payroll.salaries.manage");
      const auto user = Field(tx, data, "user_id"), account = Field(tx, data, "account_id");
      Id(user);
      Id(account);
      if (user == actor) throw DomainError(ErrorCode::kDenied, "cannot assign own salary");
      Money::Parse(Field(tx, data, "salary"));
      if (sql::Exec(tx,
                    "SELECT id FROM finance.accounts WHERE id=$1::uuid AND owner_principal_id=$2::uuid AND "
                    "account_type='personal' AND status='active'",
                    account, user)
              .empty())
        throw DomainError(ErrorCode::kConflict, "employee requires an owned active personal account");
      const auto result =
          sql::One(tx,
                   "INSERT INTO "
                   "finance.payroll_employees(organization_id,user_id,account_id,salary,frequency,position,note,"
                   "created_by,updated_by) VALUES($1::uuid,$2::uuid,$3::uuid,$4::numeric,$5,$6,$7,$8::uuid,$8::uuid) "
                   "RETURNING (to_jsonb(payroll_employees)||jsonb_build_object('salary',salary::text))::text",
                   org, user, account, Field(tx, data, "salary"), Field(tx, data, "frequency", "monthly"),
                   Field(tx, data, "position"), Field(tx, data, "note"), actor)[0]
              .as<std::string>();
      tx.commit();
      return result;
    }
    const auto old = sql::Exec(tx,
                               "SELECT user_id::text,account_id::text,salary::text,frequency FROM "
                               "finance.payroll_employees WHERE id=$1::uuid AND organization_id=$2::uuid FOR UPDATE",
                               id, org);
    if (old.empty()) throw DomainError(ErrorCode::kNotFound, "employee not found");
    const auto account = Field(tx, data, "account_id", old[0][1].c_str()),
               salary = Field(tx, data, "salary", old[0][2].c_str()),
               frequency = Field(tx, data, "frequency", old[0][3].c_str());
    if (account != old[0][1].as<std::string>() || salary != old[0][2].as<std::string>() ||
        frequency != old[0][3].as<std::string>()) {
      Require(tx, actor, org, "payroll.salaries.manage");
      if (old[0][0].as<std::string>() == actor) throw DomainError(ErrorCode::kDenied, "cannot change own salary");
    }
    Money::Parse(salary);
    Id(account);
    if (Field(tx, data, "active") != "false" &&
        sql::Exec(tx,
                  "SELECT id FROM finance.accounts WHERE id=$1::uuid AND owner_principal_id=$2::uuid AND "
                  "account_type='personal' AND status='active'",
                  account, old[0][0].c_str())
            .empty())
      throw DomainError(ErrorCode::kConflict, "employee account is invalid");
    const auto result =
        sql::One(
            tx,
            "UPDATE finance.payroll_employees SET "
            "account_id=$3::uuid,salary=$4::numeric,frequency=$5,position=COALESCE($6::jsonb->>'position',position),"
            "note=COALESCE($6::jsonb->>'note',note),active=COALESCE(($6::jsonb->>'active')::boolean,active),updated_by="
            "$7::uuid,revision=revision+1,updated_at=now() WHERE id=$1::uuid AND organization_id=$2::uuid RETURNING "
            "(to_jsonb(payroll_employees)||jsonb_build_object('salary',salary::text))::text",
            id, org, account, salary, frequency, data, actor)[0]
            .as<std::string>();
    tx.commit();
    return result;
  }
  if (action.rfind("batch.", 0) == 0) {
    if (action == "batch.resume") {
      if (!worker) throw DomainError(ErrorCode::kDenied, "worker context required");
    } else
      Require(tx, actor, org,
              action == "batch.create" || action == "batch.submit" || action == "batch.cancel"
                  ? "payroll.batches.create"
              : action == "batch.approve"                            ? "payroll.approve"
              : action == "batch.execute" || action == "batch.retry" ? "payroll.execute"
                                                                     : "payroll.read");
    if (action == "batch.create") {
      const auto batch = CreateBatch(tx, org, actor, data);
      const auto result = Batch(tx, batch);
      tx.commit();
      return result;
    }
    if (action == "batch.list") {
      const auto limit = std::clamp(std::stoi(Field(tx, data, "limit", "25")), 1, 100);
      const auto cursor = Field(tx, data, "cursor");
      if (!cursor.empty()) Id(cursor);
      const auto rows = sql::Exec(tx,
                                  "SELECT id::text FROM finance.payroll_batches WHERE organization_id=$1::uuid AND "
                                  "($2='' OR id>NULLIF($2,'')::uuid) AND ($3='' OR status=$3) ORDER BY id LIMIT $4",
                                  org, cursor, Field(tx, data, "status"), limit + 1);
      return Page(tx, rows, limit, "batch");
    }
    const auto found = sql::Exec(tx,
                                 "SELECT status,COALESCE(executor_id::text,'') FROM finance.payroll_batches WHERE "
                                 "id=$1::uuid AND organization_id=$2::uuid FOR UPDATE",
                                 id, org);
    if (found.empty()) throw DomainError(ErrorCode::kNotFound, "batch not found");
    if (action == "batch.submit") {
      if (found[0][0].as<std::string>() != "draft") throw DomainError(ErrorCode::kConflict, "draft batch required");
      sql::Exec(tx, "UPDATE finance.payroll_batches SET status='pending_approval' WHERE id=$1::uuid", id);
      Audit(tx, actor, "batch.submit", id);
      Notice(tx, org, "pending_approval", id);
    } else if (action == "batch.approve") {
      if (Field(tx, data, "confirmed") != "true") throw DomainError(ErrorCode::kDenied, "confirmation required");
      if (found[0][0].as<std::string>() != "pending_approval")
        throw DomainError(ErrorCode::kConflict, "pending approval required");
      Approve(tx, org, id, actor);
    } else if (action == "batch.execute" || action == "batch.retry" || action == "batch.resume") {
      if (action == "batch.resume") {
        if (found[0][0].as<std::string>() != "processing" || found[0][1].as<std::string>() != actor)
          throw DomainError(ErrorCode::kDenied, "persisted execution context required");
      } else {
        if (Field(tx, data, "confirmed") != "true") throw DomainError(ErrorCode::kDenied, "confirmation required");
        try {
          Begin(tx, id, org, actor, action == "batch.retry");
        } catch (const DomainError&) {
          tx.abort();
          pqxx::work notice{db};
          Notice(notice, org, "execution_error", id);
          Audit(notice, actor, "batch.execution_error", id);
          notice.commit();
          throw;
        }
      }
      tx.commit();
      Process(database, id, actor);
      pqxx::read_transaction read{db};
      return Batch(read, id);
    } else if (action == "batch.cancel") {
      if (found[0][0].as<std::string>() != "draft" && found[0][0].as<std::string>() != "pending_approval")
        throw DomainError(ErrorCode::kConflict, "execution already started");
      sql::Exec(tx, "UPDATE finance.payroll_batches SET status='cancelled' WHERE id=$1::uuid", id);
      Audit(tx, actor, "batch.cancel", id);
    } else if (action != "batch.get")
      throw std::invalid_argument("unknown batch action");
    const auto result = Batch(tx, id);
    tx.commit();
    return result;
  }
  if (action.rfind("schedule.", 0) == 0) {
    if (action != "schedule.fire")
      Require(tx, actor, org,
              action == "schedule.list" || action == "schedule.get" ? "payroll.read" : "payroll.automatic.manage");
    else if (!worker)
      throw DomainError(ErrorCode::kDenied, "worker context required");
    if (action == "schedule.list") {
      const auto limit = std::clamp(std::stoi(Field(tx, data, "limit", "25")), 1, 100);
      const auto cursor = Field(tx, data, "cursor");
      if (!cursor.empty()) Id(cursor);
      return Page(tx,
                  sql::Exec(tx,
                            "SELECT id::text FROM finance.payroll_schedules WHERE organization_id=$1::uuid AND ($2='' "
                            "OR id>NULLIF($2,'')::uuid) ORDER BY id LIMIT $3",
                            org, cursor, limit + 1),
                  limit, "schedule");
    }
    std::string schedule = id;
    if (action == "schedule.create" || action == "schedule.update") {
      Require(tx, actor, org, "payroll.batches.create");
      const auto source = Field(tx, data, "source_account_id");
      Source(tx, org, source);
      const auto items = Snapshot(tx, org, data, actor);
      const auto mode = Field(tx, data, "mode");
      const auto max = Money::Parse(Field(tx, data, "max_amount"));
      Money total;
      for (const auto& row : items) total = total + Money::Parse(row[3].c_str());
      if (max < total) throw DomainError(ErrorCode::kLimit, "schedule total exceeds automatic cap");
      if (action == "schedule.create")
        schedule =
            sql::One(tx,
                     "INSERT INTO "
                     "finance.payroll_schedules(organization_id,source_account_id,name,frequency,weekday,month_day,"
                     "local_time,timezone,mode,max_amount,next_run_at,created_by,status) "
                     "VALUES($1::uuid,$2::uuid,$3,$4,$5::int,$6::int,$7::time,$8,$9,$10::numeric,$11::timestamptz,$12::"
                     "uuid,CASE WHEN $9='manual' THEN 'active' ELSE 'paused' END) RETURNING id::text",
                     org, source, Field(tx, data, "name"), Field(tx, data, "frequency"),
                     Field(tx, data, "weekday", "1"), Field(tx, data, "month_day", "1"), Field(tx, data, "local_time"),
                     Field(tx, data, "timezone"), mode, max.ToString(), Field(tx, data, "next_run_at"), actor)[0]
                .as<std::string>();
      else {
        const auto updated =
            sql::Exec(tx,
                      "UPDATE finance.payroll_schedules SET "
                      "source_account_id=$3::uuid,name=$4,frequency=$5,weekday=$6::int,month_day=$7::int,local_time=$8:"
                      ":time,timezone=$9,mode=$10,max_amount=$11::numeric,next_run_at=$12::timestamptz,created_by=$13::"
                      "uuid,approved_by=NULL,approved_at=NULL,status='paused',revision=revision+1,last_error=NULL "
                      "WHERE id=$1::uuid AND organization_id=$2::uuid AND status<>'disabled' RETURNING id",
                      id, org, source, Field(tx, data, "name"), Field(tx, data, "frequency"),
                      Field(tx, data, "weekday", "1"), Field(tx, data, "month_day", "1"), Field(tx, data, "local_time"),
                      Field(tx, data, "timezone"), mode, max.ToString(), Field(tx, data, "next_run_at"), actor);
        if (updated.empty()) throw DomainError(ErrorCode::kNotFound, "enabled schedule not found");
        sql::Exec(tx, "DELETE FROM finance.payroll_schedule_items WHERE schedule_id=$1::uuid", id);
      }
      for (const auto& row : items)
        sql::Exec(tx,
                  "INSERT INTO "
                  "finance.payroll_schedule_items(schedule_id,employee_id,employee_revision,user_id,account_id,amount) "
                  "VALUES($1::uuid,$2::uuid,$3::int,$4::uuid,$5::uuid,$6::numeric)",
                  schedule, row[0].c_str(), row[4].c_str(), row[1].c_str(), row[2].c_str(), row[3].c_str());
      Audit(tx, actor, action, schedule, data);
    } else {
      const auto s = sql::Exec(
          tx,
          "SELECT "
          "created_by::text,COALESCE(approved_by::text,''),mode,status,source_account_id::text,next_run_at::text,"
          "revision FROM finance.payroll_schedules WHERE id=$1::uuid AND organization_id=$2::uuid FOR UPDATE",
          id, org);
      if (s.empty()) throw DomainError(ErrorCode::kNotFound, "schedule not found");
      if (action == "schedule.approve") {
        if (Field(tx, data, "confirmed") != "true" || Field(tx, data, "revision") != s[0][6].as<std::string>())
          throw DomainError(ErrorCode::kConflict, "current schedule revision confirmation required");
        Require(tx, actor, org, "payroll.approve");
        Require(tx, actor, org, "payroll.execute");
        if (!IsOwner(tx, actor, org) && (actor == s[0][0].as<std::string>() ||
                                         sql::One(tx,
                                                  "SELECT EXISTS(SELECT 1 FROM finance.payroll_schedule_items "
                                                  "WHERE schedule_id=$1::uuid AND user_id=$2::uuid)",
                                                  id, actor)[0]
                                             .as<bool>()))
          throw DomainError(ErrorCode::kDenied, "separate non-recipient schedule approver required");
        if (sql::One(tx,
                     "SELECT EXISTS(SELECT 1 FROM finance.payroll_schedule_items i JOIN finance.payroll_employees e ON "
                     "e.id=i.employee_id WHERE i.schedule_id=$1::uuid AND (e.revision<>i.employee_revision OR NOT "
                     "e.active))",
                     id)[0]
                .as<bool>())
          throw DomainError(ErrorCode::kConflict, "save a refreshed employee snapshot before approval");
        sql::Exec(tx,
                  "UPDATE finance.payroll_schedules SET "
                  "approved_by=$2::uuid,approved_at=now(),status='active',last_error=NULL WHERE id=$1::uuid",
                  id, actor);
      } else if (action == "schedule.state") {
        const auto state = Field(tx, data, "status");
        if (state != "active" && state != "paused" && state != "disabled")
          throw std::invalid_argument("invalid schedule state");
        if (s[0][3].as<std::string>() == "disabled")
          throw DomainError(ErrorCode::kConflict, "disabled schedule is final");
        if (state == "active" && s[0][2].as<std::string>() == "automatic" && s[0][1].as<std::string>().empty())
          throw DomainError(ErrorCode::kDenied, "automatic approval required");
        sql::Exec(tx, "UPDATE finance.payroll_schedules SET status=$2 WHERE id=$1::uuid", id, state);
        if (state != "active") Notice(tx, org, "schedule_paused", id);
      } else if (action == "schedule.fire") {
        if (s[0][3].as<std::string>() != "active" ||
            !sql::One(tx,
                      "SELECT $1::timestamptz=$2::timestamptz AND $1::timestamptz<=clock_timestamp() AND "
                      "$3::timestamptz>clock_timestamp()",
                      s[0][5].c_str(), Field(tx, data, "scheduled_for"), Field(tx, data, "next_run_at"))[0]
                 .as<bool>())
          throw DomainError(ErrorCode::kConflict, "schedule occurrence already handled or not due");
        const auto creator = s[0][0].as<std::string>(), approver = s[0][1].as<std::string>();
        Require(tx, creator, org, "payroll.automatic.manage");
        Require(tx, creator, org, "payroll.batches.create");
        if (s[0][2].as<std::string>() == "automatic") {
          Require(tx, approver, org, "payroll.automatic.manage");
          Require(tx, approver, org, "payroll.approve");
          Require(tx, approver, org, "payroll.execute");
        }
        if (sql::One(tx,
                     "SELECT EXISTS(SELECT 1 FROM finance.payroll_schedule_items i JOIN finance.payroll_employees e ON "
                     "e.id=i.employee_id WHERE i.schedule_id=$1::uuid AND (e.revision<>i.employee_revision OR NOT "
                     "e.active))",
                     id)[0]
                .as<bool>())
          throw DomainError(ErrorCode::kConflict, "employee snapshot changed");
        const auto payload =
            sql::One(tx,
                     "SELECT "
                     "jsonb_build_object('source_account_id',s.source_account_id,'description',s.name,'creation_key','"
                     "schedule:'||s.id||':'||s.next_run_at::text,'items',"
                     "(SELECT jsonb_agg(jsonb_build_object('employee_id',employee_id,'amount',amount::text) ORDER BY "
                     "employee_id) FROM finance.payroll_schedule_items WHERE schedule_id=s.id))::text FROM "
                     "finance.payroll_schedules s WHERE id=$1::uuid",
                     id)[0]
                .as<std::string>();
        const auto batch = CreateBatch(tx, org, creator, payload, id, s[0][5].c_str());
        sql::Exec(tx, "UPDATE finance.payroll_batches SET status='pending_approval' WHERE id=$1::uuid", batch);
        if (s[0][2].as<std::string>() == "automatic") Approve(tx, org, batch, approver);
        sql::Exec(tx, "UPDATE finance.payroll_schedules SET next_run_at=$2::timestamptz WHERE id=$1::uuid", id,
                  Field(tx, data, "next_run_at"));
        Notice(tx, org, "pending_approval", batch);
        Audit(tx, creator, "schedule.fire", id);
        const auto result =
            sql::One(tx,
                     "SELECT jsonb_build_object('batch_id',$1::text,'actor',$2::text,'automatic',$3::boolean)::text",
                     batch, approver, s[0][2].as<std::string>() == "automatic")[0]
                .as<std::string>();
        tx.commit();
        return result;
      } else if (action != "schedule.get")
        throw std::invalid_argument("unknown schedule action");
      if (action != "schedule.get") Audit(tx, actor, action, id, data);
    }
    const auto result = Schedule(tx, schedule);
    tx.commit();
    return result;
  }
  throw std::invalid_argument("unknown business action");
}
}  // namespace horizon

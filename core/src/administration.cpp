#include "horizon/administration.hpp"

#include <algorithm>
#include <pqxx/pqxx>
#include <regex>
#include <set>

#include "horizon/error.hpp"
#include "horizon/sql.hpp"

namespace horizon {
namespace pb = ::horizon::banking::v1;
namespace {
void Id(const std::string& value) {
  static const std::regex pattern("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$");
  if (!std::regex_match(value, pattern)) throw std::invalid_argument("canonical UUID required");
}
bool Authority(pqxx::transaction_base& tx, const std::string& actor, const std::string& permission) {
  return sql::One(tx,
                  "SELECT EXISTS(SELECT 1 FROM finance.authorities WHERE principal_id=$1::uuid AND permission=$2 AND "
                  "revoked_at IS NULL)",
                  actor, permission)[0]
      .as<bool>();
}
void Require(pqxx::transaction_base& tx, const std::string& actor, const std::string& permission) {
  if (!Authority(tx, actor, permission))
    throw DomainError(ErrorCode::kDenied, "core financial authority is not granted");
}
std::string Page(const pqxx::result& rows, pqxx::result::size_type limit) {
  std::string json = "{\"items\":[";
  for (pqxx::result::size_type i = 0; i < rows.size() && i < limit; ++i) {
    if (i) json += ",";
    json += rows[i][1].c_str();
  }
  json += "],\"next_cursor\":";
  if (rows.size() > limit) {
    json += '"';
    json += rows[limit - 1][0].as<std::string>();
    json += '"';
  } else {
    json += "null";
  }
  json += '}';
  return json;
}
}  // namespace

std::string Administration::Query(const std::string& database, const pb::AdminQueryRequest& r) {
  if (r.query().size() > 100 || r.from_date().size() > 40 || r.to_date().size() > 40)
    throw std::invalid_argument("invalid admin filter");
  if (!r.target_id().empty()) Id(r.target_id());
  if (!r.cursor().empty()) Id(r.cursor());
  pqxx::connection db{database};
  pqxx::read_transaction tx{db};
  Require(tx, r.context().actor_id(), "admin.read");
  const auto limit = static_cast<pqxx::result::size_type>(std::clamp(r.limit(), 1U, 100U));
  if (r.kind() == "overview")
    return sql::One(tx,
                    "SELECT jsonb_build_object('accounts',(SELECT count(*) FROM finance.accounts WHERE "
                    "account_type<>'system'),'operations',(SELECT count(*) FROM "
                    "finance.financial_operations),'core','ready')::text")[0]
        .c_str();
  if (r.kind() == "accounts") {
    const auto rows = sql::Exec(
        tx,
        "SELECT x.id::text,to_jsonb(x)::text FROM (SELECT "
        "a.id,a.name,a.public_number,a.account_type,a.status,a.owner_principal_id "
        "owner_id,a.organization_id,a.balance::text balance,a.held::text held,COALESCE(o.name,'') organization "
        "FROM finance.accounts a LEFT JOIN finance.organizations o ON o.id=a.organization_id WHERE "
        "a.account_type<>'system' "
        "AND ($1='' OR position(lower($1) in lower(a.name||' '||a.public_number||' '||COALESCE(o.name,'')))>0 OR "
        "a.id::text=$1) "
        "AND ($2='' OR a.owner_principal_id=NULLIF($2,'')::uuid) AND ($3='' OR a.id>NULLIF($3,'')::uuid) ORDER BY a.id "
        "LIMIT $4) x",
        r.query(), r.target_id(), r.cursor(), limit + 1);
    return Page(rows, limit);
  }
  if (r.kind() == "operations") {
    const auto rows = sql::Exec(
        tx,
        "SELECT x.id::text,to_jsonb(x)::text FROM (SELECT o.id,o.operation_type type,o.status,o.amount::text "
        "amount,'DB' currency,o.source_account_id,o.destination_account_id,o.reason,o.created_at,o.completed_at, "
        "s.name source_name,s.public_number source_public_number,d.name destination_name,d.public_number "
        "destination_public_number,o.related_operation_id "
        "FROM finance.financial_operations o LEFT JOIN finance.accounts s ON s.id=o.source_account_id LEFT JOIN "
        "finance.accounts d ON d.id=o.destination_account_id "
        "WHERE ($1='' OR o.id::text=$1 OR position(lower($1) in lower(o.reason||' '||COALESCE(s.public_number,'')||' "
        "'||COALESCE(d.public_number,'')))>0) "
        "AND ($2='' OR o.source_account_id=NULLIF($2,'')::uuid OR o.destination_account_id=NULLIF($2,'')::uuid) "
        "AND ($3='' OR (o.created_at,o.id)<(SELECT created_at,id FROM finance.financial_operations WHERE "
        "id=NULLIF($3,'')::uuid)) "
        "AND ($4='' OR o.operation_type=$4) AND ($5='' OR o.created_at>=NULLIF($5,'')::timestamptz) AND ($6='' OR "
        "o.created_at<NULLIF($6,'')::timestamptz) "
        "ORDER BY o.created_at DESC,o.id DESC LIMIT $7) x",
        r.query(), r.target_id(), r.cursor(), r.operation_type(), r.from_date(), r.to_date(), limit + 1);
    return Page(rows, limit);
  }
  if (r.kind() == "permissions") {
    const auto target = r.target_id().empty() ? r.context().actor_id() : r.target_id();
    return sql::One(tx,
                    "SELECT jsonb_build_object('authorities',COALESCE((SELECT jsonb_agg(permission ORDER BY "
                    "permission) FROM finance.authorities WHERE principal_id=$1::uuid AND revoked_at IS "
                    "NULL),'[]'::jsonb),'account_permissions',COALESCE((SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT "
                    "p.id,p.account_id,a.name,a.public_number,p.permission FROM finance.account_permissions p JOIN "
                    "finance.accounts a ON a.id=p.account_id WHERE p.principal_id=$1::uuid AND p.revoked_at IS NULL "
                    "AND ($2='' OR p.id>NULLIF($2,'')::uuid) ORDER BY p.id LIMIT $3) x),'[]'::jsonb))::text",
                    target, r.cursor(), limit)[0]
        .c_str();
  }
  throw std::invalid_argument("unknown administrative query");
}

std::string Administration::Change(const std::string& database, const pb::AdminChangeRequest& r) {
  if (!r.confirmed() || r.reason().empty() || r.reason().size() > 2000)
    throw std::invalid_argument("confirmation and reason required");
  const auto actor = r.context().actor_id();
  pqxx::connection db{database};
  pqxx::work tx{db};
  sql::Exec(tx, "SELECT pg_advisory_xact_lock(220024)");
  if (r.kind() == "state") {
    Require(tx, actor, "account.state.manage");
    Id(r.account_id());
    if (r.state() != "active" && r.state() != "frozen" && r.state() != "closed")
      throw std::invalid_argument("invalid account status");
    const auto found = sql::Exec(tx,
                                 "SELECT status,balance::text,held::text FROM finance.accounts WHERE id=$1::uuid AND "
                                 "account_type<>'system' FOR UPDATE",
                                 r.account_id());
    if (found.empty()) throw DomainError(ErrorCode::kNotFound, "account not found");
    if (found[0][0].as<std::string>() == "closed" && r.state() != "closed")
      throw DomainError(ErrorCode::kConflict, "closed account cannot be reopened");
    if (r.state() == "closed" && (found[0][1].as<std::string>() != "0.00" || found[0][2].as<std::string>() != "0.00"))
      throw DomainError(ErrorCode::kConflict, "only empty accounts can be closed");
    sql::Exec(tx, "UPDATE finance.accounts SET status=$2,updated_at=now() WHERE id=$1::uuid", r.account_id(),
              r.state());
  } else if (r.kind() == "permission") {
    Require(tx, actor, "authority.manage");
    Id(r.target_id());
    if (actor == r.target_id()) throw DomainError(ErrorCode::kDenied, "cannot change own financial grants");
    const std::set<std::string> global{"issue.create",     "refund.create",        "admin.read",
                                       "authority.manage", "account.state.manage", "exchange.manage"};
    const std::set<std::string> account{"accounts.read", "balance.read",   "history.read",
                                        "transfer",      "payment.create", "hold.create",
                                        "refund.create", "issue.create",   "account.manage"};
    if (r.account_id().empty()) {
      if (!global.contains(r.permission())) throw std::invalid_argument("unknown authority");
      Require(tx, actor, r.permission());
      if (r.revoke())
        sql::Exec(tx, "UPDATE finance.authorities SET revoked_at=now() WHERE principal_id=$1::uuid AND permission=$2",
                  r.target_id(), r.permission());
      else
        sql::Exec(tx,
                  "INSERT INTO finance.authorities(principal_id,permission,granted_by,reason) "
                  "VALUES($1::uuid,$2,$3::uuid,$4) ON CONFLICT(principal_id,permission) DO UPDATE SET "
                  "revoked_at=NULL,granted_by=EXCLUDED.granted_by,reason=EXCLUDED.reason,created_at=now()",
                  r.target_id(), r.permission(), actor, r.reason());
    } else {
      Id(r.account_id());
      if (!account.contains(r.permission())) throw std::invalid_argument("unknown account permission");
      if (!sql::One(tx,
                    "SELECT EXISTS(SELECT 1 FROM finance.account_permissions p JOIN finance.accounts a ON "
                    "a.id=p.account_id WHERE a.account_type<>'system' AND p.account_id=$1::uuid AND "
                    "p.principal_id=$2::uuid AND p.permission=$3 AND p.revoked_at IS NULL)",
                    r.account_id(), actor, r.permission())[0]
               .as<bool>())
        throw DomainError(ErrorCode::kDenied, "cannot delegate an account permission not held");
      if (r.revoke())
        sql::Exec(tx,
                  "UPDATE finance.account_permissions SET revoked_at=now() WHERE principal_id=$1::uuid AND "
                  "account_id=$2::uuid AND permission=$3",
                  r.target_id(), r.account_id(), r.permission());
      else
        sql::Exec(tx,
                  "INSERT INTO finance.account_permissions(account_id,principal_id,permission,granted_by) "
                  "VALUES($1::uuid,$2::uuid,$3,$4::uuid) ON CONFLICT(account_id,principal_id,permission) DO UPDATE SET "
                  "revoked_at=NULL,granted_by=EXCLUDED.granted_by,created_at=now()",
                  r.account_id(), r.target_id(), r.permission(), actor);
    }
    sql::Exec(tx,
              "INSERT INTO finance.event_outbox(event_key,principal_id,category,payload) "
              "VALUES('permission:'||gen_random_uuid(),$1::uuid,'security',jsonb_build_object('type','permission."
              "changed','date',now()))",
              r.target_id());
  } else
    throw std::invalid_argument("unknown administrative action");
  sql::Exec(tx,
            "INSERT INTO finance.profile_events(actor_id,action,resource_id,metadata) "
            "VALUES($1::uuid,$2,$3::uuid,jsonb_build_object('permission',$4::text,'revoke',$5::boolean,'state',$6::"
            "text,'reason',$7::text))",
            actor, "admin." + r.kind(), r.kind() == "state" ? r.account_id() : r.target_id(), r.permission(),
            r.revoke(), r.state(), r.reason());
  tx.commit();
  return "{\"ok\":true}";
}

std::string Administration::Events(const std::string& database) {
  pqxx::connection db{database};
  pqxx::work tx{db};
  sql::Exec(tx,
            "UPDATE finance.payment_requests SET status='expired' WHERE id IN "
            "(SELECT id FROM finance.payment_requests WHERE status='pending' AND expires_at<=clock_timestamp() "
            "ORDER BY expires_at,id LIMIT 100 FOR UPDATE SKIP LOCKED)");
  const auto result =
      sql::One(tx,
               "SELECT jsonb_build_object('items',COALESCE(jsonb_agg(to_jsonb(x) ORDER BY x.id),'[]'::jsonb))::text "
               "FROM (SELECT id,event_key,principal_id,category,payload,created_at FROM finance.event_outbox WHERE "
               "acknowledged_at IS NULL ORDER BY id LIMIT 100) x")[0]
          .as<std::string>();
  tx.commit();
  return result;
}
void Administration::Acknowledge(const std::string& database, const pb::NotificationEventsRequest& r) {
  if (r.acknowledged_ids_size() > 100) throw std::invalid_argument("event batch too large");
  pqxx::connection db{database};
  pqxx::work tx{db};
  for (auto id : r.acknowledged_ids())
    sql::Exec(tx, "UPDATE finance.event_outbox SET acknowledged_at=COALESCE(acknowledged_at,now()) WHERE id=$1", id);
  tx.commit();
}
}  // namespace horizon

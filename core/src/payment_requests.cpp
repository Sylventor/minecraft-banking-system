#include "horizon/payment_requests.hpp"

#include <algorithm>
#include <regex>
#include <vector>

#include "horizon/error.hpp"
#include "horizon/postgres_ledger.hpp"
#include "horizon/sql.hpp"

namespace horizon {
namespace {
using Request = banking::v1::PaymentRequestCommand;
void Id(const std::string& value) {
  static const std::regex uuid("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$");
  if (!std::regex_match(value, uuid)) throw std::invalid_argument("canonical UUID required");
}
bool Permission(pqxx::transaction_base& tx, const std::string& actor, const std::string& account,
                const std::string& permission) {
  return !sql::Exec(tx,
                    "SELECT id FROM finance.account_permissions WHERE principal_id=$1::uuid AND "
                    "account_id=$2::uuid AND permission=$3 AND revoked_at IS NULL FOR SHARE",
                    actor, account, permission)
              .empty();
}
std::string Accounts(const Request& r) {
  std::string accounts;
  for (const auto& id : r.account_ids()) {
    Id(id);
    if (!accounts.empty()) accounts += ',';
    accounts += id;
  }
  return accounts;
}
bool Merchant(const Request& r, const std::string& account, const std::string& org, const std::string& key) {
  if (r.key_id().empty()) return true;
  return !org.empty() && org == r.organization_id() && key == r.key_id() &&
         std::find(r.account_ids().begin(), r.account_ids().end(), account) != r.account_ids().end();
}
std::string View(pqxx::transaction_base& tx, const std::string& id) {
  return sql::One(tx,
                  "SELECT ((to_jsonb(p)-ARRAY['fingerprint','creation_key','pay_key','creator_key_id'])||"
                  "jsonb_build_object('status',CASE WHEN p.status='pending' AND p.expires_at<=clock_timestamp() "
                  "THEN 'expired' ELSE p.status END,'amount',p.amount::text,'merchant_name',COALESCE(o.name,a.name),"
                  "'recipient_account_name',a.name,'recipient_public_number',a.public_number))::text "
                  "FROM finance.payment_requests p JOIN finance.accounts a ON a.id=p.recipient_account_id "
                  "LEFT JOIN finance.organizations o ON o.id=p.organization_id WHERE p.id=$1::uuid",
                  id)[0]
      .as<std::string>();
}
void Expire(pqxx::transaction_base& tx, const std::string& id) {
  sql::Exec(tx,
            "UPDATE finance.payment_requests SET status='expired' WHERE id=$1::uuid "
            "AND status='pending' AND expires_at<=clock_timestamp()",
            id);
}
}  // namespace

std::string PaymentRequests::Execute(const std::string& database, const Request& r) {
  const auto actor = r.context().actor_id();
  if (!r.id().empty()) Id(r.id());
  if (!r.organization_id().empty()) Id(r.organization_id());
  if (!r.key_id().empty()) Id(r.key_id());
  if (r.action() == "pay") {
    if (!r.key_id().empty() || !r.confirmed())
      throw DomainError(ErrorCode::kDenied, "interactive payer confirmation required");
    Id(r.source_account_id());
    TransferCommand cmd;
    cmd.actor_id = actor;
    cmd.source_account_id = r.source_account_id();
    cmd.idempotency_key = r.idempotency_key();
    cmd.fingerprint = r.fingerprint();
    cmd.request_id = r.context().request_id();
    cmd.correlation_id = r.context().correlation_id();
    cmd.source = "web";
    try {
      PostgresLedger(database).PayRequest(cmd, r.id());
    } catch (const DomainError&) {
      pqxx::connection db{database};
      pqxx::work tx{db};
      sql::Exec(tx,
                "INSERT INTO finance.profile_events(actor_id,action,resource_id) SELECT $1::uuid,"
                "'payment_request.error',id FROM finance.payment_requests WHERE id=$2::uuid AND payer_id=$1::uuid",
                actor, r.id());
      tx.commit();
      throw;
    }
    pqxx::connection db{database};
    pqxx::read_transaction tx{db};
    return View(tx, r.id());
  }
  pqxx::connection db{database};
  pqxx::work tx{db};
  sql::Exec(tx, "SELECT set_config('horizon.actor_id',$1,true)", actor);
  if (r.action() == "create") {
    Id(r.payer_id());
    Id(r.recipient_account_id());
    if (r.payer_id() == actor || r.idempotency_key().size() < 16 || r.idempotency_key().size() > 255 ||
        r.fingerprint().size() != 64 || r.description().empty() || r.description().size() > 2000 ||
        r.external_id().size() > 120 || r.metadata().size() > 8192)
      throw std::invalid_argument("invalid payment request");
    const auto amount = Money::Parse(r.amount());
    if (amount.cents() == 0) throw std::invalid_argument("amount must be positive");
    std::vector<std::string> locks{"payment-request-create:" + actor,
                                   "payment-request-account:" + r.recipient_account_id(),
                                   "payment-request-payer:" + r.payer_id()};
    std::sort(locks.begin(), locks.end());
    for (const auto& key : locks) sql::Exec(tx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", key);
    const auto accounts =
        sql::Exec(tx,
                  "SELECT COALESCE(organization_id::text,''),COALESCE(owner_principal_id::text,''),status "
                  "FROM finance.accounts WHERE id=$1::uuid AND account_type IN ('personal','organization') FOR SHARE",
                  r.recipient_account_id());
    if (accounts.empty()) throw DomainError(ErrorCode::kNotFound, "recipient account not found");
    const auto org = accounts[0][0].as<std::string>();
    if (!Merchant(r, r.recipient_account_id(), org, r.key_id()) || accounts[0][1].as<std::string>() == r.payer_id() ||
        !Permission(tx, actor, r.recipient_account_id(), "payment_requests.create"))
      throw DomainError(ErrorCode::kDenied, "request creation is not granted");
    if (accounts[0][2].as<std::string>() != "active")
      throw DomainError(ErrorCode::kConflict, "recipient account is not active");
    const auto digest =
        sql::One(tx,
                 "SELECT encode(sha256(convert_to(json_build_array($1::text,$2::text,$3::text,$4::text,"
                 "$5::text,$6::text,$7::text,$8::text,$9::text,$10::text)::text,'UTF8')),'hex')",
                 r.fingerprint(), r.payer_id(), r.recipient_account_id(), r.amount(), r.description(), r.expires_at(),
                 std::to_string(r.expires_in()), r.external_id(), r.metadata(), r.key_id())[0]
            .as<std::string>();
    const auto prior =
        sql::Exec(tx,
                  "SELECT id::text,fingerprint,COALESCE(creator_key_id::text,'') FROM finance.payment_requests "
                  "WHERE creator_id=$1::uuid AND creation_key=$2",
                  actor, r.idempotency_key());
    if (!prior.empty()) {
      if (prior[0][1].as<std::string>() != digest || prior[0][2].as<std::string>() != r.key_id())
        throw DomainError(ErrorCode::kConflict, "idempotency payload mismatch");
      auto result = View(tx, prior[0][0].c_str());
      tx.commit();
      return result;
    }
    if ((!r.expires_at().empty() && r.expires_in() != 0) ||
        (r.expires_at().empty() && (r.expires_in() < 60 || r.expires_in() > 2592000)))
      throw std::invalid_argument("invalid expiry");
    if (sql::One(tx,
                 "SELECT count(*) FROM finance.payment_requests WHERE creator_id=$1::uuid AND "
                 "created_at>clock_timestamp()-interval '1 minute'",
                 actor)[0]
                .as<int>() >= 20 ||
        sql::One(tx,
                 "SELECT count(*) FROM finance.payment_requests WHERE recipient_account_id=$1::uuid AND "
                 "status='pending' AND expires_at>clock_timestamp()",
                 r.recipient_account_id())[0]
                .as<int>() >= 100 ||
        sql::One(tx,
                 "SELECT count(*) FROM finance.payment_requests WHERE payer_id=$1::uuid AND "
                 "status='pending' AND expires_at>clock_timestamp()",
                 r.payer_id())[0]
                .as<int>() >= 100)
      throw DomainError(ErrorCode::kLimit, "payment request limit exceeded");
    if (!r.external_id().empty() &&
        !sql::Exec(tx,
                   "SELECT id FROM finance.payment_requests WHERE recipient_account_id=$1::uuid "
                   "AND external_id=$2",
                   r.recipient_account_id(), r.external_id())
             .empty())
      throw DomainError(ErrorCode::kConflict, "external order already exists");
    const auto expiry =
        sql::One(tx, "SELECT COALESCE(NULLIF($1,'')::timestamptz,clock_timestamp()+$2::int*interval '1 second')::text",
                 r.expires_at(), r.expires_in())[0]
            .as<std::string>();
    if (!sql::One(tx,
                  "SELECT $1::timestamptz>clock_timestamp() AND $1::timestamptz<=clock_timestamp()+interval '30 days'",
                  expiry)[0]
             .as<bool>())
      throw std::invalid_argument("expiry must be within 30 days");
    const auto id =
        sql::One(
            tx,
            "INSERT INTO finance.payment_requests(creator_id,creator_key_id,organization_id,payer_id,"
            "recipient_account_id,amount,description,expires_at,external_id,metadata,creation_key,fingerprint) "
            "VALUES($1::uuid,NULLIF($2,'')::uuid,NULLIF($3,'')::uuid,$4::uuid,$5::uuid,$6::numeric,$7,$8::timestamptz,"
            "NULLIF($9,''),$10::jsonb,$11,$12) RETURNING id::text",
            actor, r.key_id(), org, r.payer_id(), r.recipient_account_id(), r.amount(), r.description(), expiry,
            r.external_id(), r.metadata().empty() ? "{}" : r.metadata(), r.idempotency_key(), digest)[0]
            .as<std::string>();
    auto result = View(tx, id);
    tx.commit();
    return result;
  }
  if (r.action() == "list") {
    if (r.offset() > 1000000 ||
        (r.direction() != "incoming" && r.direction() != "outgoing" && r.direction() != "history"))
      throw std::invalid_argument("invalid pagination");
    const auto limit = static_cast<pqxx::result::size_type>(std::clamp(r.limit(), 1U, 100U));
    const std::string order = r.order() == "asc" ? " ASC" : " DESC";
    const std::string query =
        "SELECT p.id::text,count(*) OVER() FROM finance.payment_requests p WHERE "
        "(($2='incoming' AND p.payer_id=$1::uuid AND $7='') OR ($2='outgoing' AND EXISTS(SELECT 1 FROM "
        "finance.account_permissions a "
        "WHERE a.account_id=p.recipient_account_id AND a.principal_id=$1::uuid AND "
        "(a.permission='payment_requests.read' OR (p.creator_id=$1::uuid AND a.permission='payment_requests.create')) "
        "AND a.revoked_at IS NULL)) "
        "OR ($2='history' AND $7='' AND (p.payer_id=$1::uuid OR EXISTS(SELECT 1 FROM finance.account_permissions a "
        "WHERE "
        "a.account_id=p.recipient_account_id AND a.principal_id=$1::uuid AND (a.permission='payment_requests.read' OR "
        "(p.creator_id=$1::uuid AND a.permission='payment_requests.create')) AND "
        "a.revoked_at IS NULL)))) "
        "AND ($3='' OR (CASE WHEN status='pending' AND expires_at<=clock_timestamp() THEN 'expired' ELSE status "
        "END)=$3) "
        "AND ($2<>'history' OR status<>'pending' OR expires_at<=clock_timestamp()) "
        "AND ($4='' OR position(lower($4) in lower(COALESCE(external_id,'')))>0) "
        "AND ($5='' OR organization_id=NULLIF($5,'')::uuid) "
        "AND ($7='' OR (creator_key_id=NULLIF($7,'')::uuid AND organization_id=NULLIF($5,'')::uuid "
        "AND recipient_account_id=ANY(string_to_array($8,',')::uuid[]))) "
        "ORDER BY created_at" +
        order + ",id" + order + " LIMIT $6 OFFSET $9";
    const auto items = sql::Exec(tx, query, actor, r.direction(), r.status(), r.external_id(), r.organization_id(),
                                 limit + 1, r.key_id(), Accounts(r), r.offset());
    auto total = items.empty() ? std::string("0") : items[0][1].as<std::string>();
    if (items.empty() && r.offset() > 0) {
      const auto first = sql::Exec(tx, query, actor, r.direction(), r.status(), r.external_id(), r.organization_id(), 1,
                                   r.key_id(), Accounts(r), 0);
      if (!first.empty()) total = first[0][1].as<std::string>();
    }
    std::string result = "{\"items\":[";
    for (pqxx::result::size_type i = 0; i < items.size() && i < limit; ++i) {
      if (i) result += ',';
      result += View(tx, items[i][0].c_str());
    }
    result += "],\"total\":" + total;
    result += ",\"next_offset\":";
    result += items.size() > limit ? std::to_string(r.offset() + limit) : "null";
    result += '}';
    tx.commit();
    return result;
  }
  const auto found = sql::Exec(
      tx,
      "SELECT payer_id::text,creator_id::text,recipient_account_id::text,"
      "COALESCE(organization_id::text,''),COALESCE(creator_key_id::text,''),status FROM finance.payment_requests "
      "WHERE id=$1::uuid FOR UPDATE",
      r.id());
  if (found.empty()) throw DomainError(ErrorCode::kNotFound, "payment request not found");
  const auto row = found[0];
  const bool payer = r.key_id().empty() && row[0].as<std::string>() == actor;
  const auto account = row[2].as<std::string>();
  const bool merchant = Merchant(r, account, row[3].as<std::string>(), row[4].as<std::string>());
  const bool own = row[1].as<std::string>() == actor && Permission(tx, actor, account, "payment_requests.create");
  const bool read = merchant && (Permission(tx, actor, account, "payment_requests.read") || own);
  if (!payer && !read) throw DomainError(ErrorCode::kNotFound, "payment request not found");
  Expire(tx, r.id());
  if (r.action() == "decline" || r.action() == "cancel") {
    if ((r.action() == "decline" && !payer) ||
        (r.action() == "cancel" && !(merchant && (Permission(tx, actor, account, "payment_requests.cancel") || own))))
      throw DomainError(ErrorCode::kDenied, "request action is not granted");
    const auto state =
        sql::One(tx, "SELECT status FROM finance.payment_requests WHERE id=$1::uuid", r.id())[0].as<std::string>();
    const auto desired = r.action() == "decline" ? "declined" : "cancelled";
    if (state == "pending") {
      std::string update = "UPDATE finance.payment_requests SET status=$2,";
      update += r.action() == "decline" ? "declined_at" : "cancelled_at";
      update += "=clock_timestamp() WHERE id=$1::uuid";
      sql::Exec(tx, update, r.id(), desired);
    } else if (state != desired) {
      tx.commit();
      throw DomainError(ErrorCode::kConflict, "payment request is final");
    }
  } else if (r.action() != "get")
    throw std::invalid_argument("unknown payment request action");
  auto result = View(tx, r.id());
  tx.commit();
  return result;
}
}  // namespace horizon

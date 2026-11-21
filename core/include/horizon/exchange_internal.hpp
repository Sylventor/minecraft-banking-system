#pragma once
#include <algorithm>
#include <cstdint>
#include <map>

#include "horizon/business_internal.hpp"
#include "horizon/exchange.hpp"
#include "horizon/postgres_ledger.hpp"

namespace horizon::exchange {
using namespace business;
using Request = banking::v1::BusinessRequest;
inline std::string NewId(pqxx::work& tx) { return sql::One(tx, "SELECT gen_random_uuid()::text")[0].as<std::string>(); }
inline void Fail(const std::string& message) { throw DomainError(ErrorCode::kConflict, message); }
inline void Authority(pqxx::work& tx, const std::string& actor, const std::string& permission) {
  if (sql::Exec(tx,
                "SELECT principal_id FROM finance.authorities WHERE principal_id=$1::uuid AND permission=$2 AND "
                "revoked_at IS NULL FOR SHARE",
                actor, permission)
          .empty())
    throw DomainError(ErrorCode::kDenied, "exchange core authority required");
}
inline bool Has(pqxx::work& tx, const std::string& actor, const std::string& account) {
  return sql::One(tx,
                  "SELECT EXISTS(SELECT 1 FROM finance.account_permissions p JOIN finance.accounts a ON "
                  "a.id=p.account_id WHERE p.principal_id=$1::uuid AND p.account_id=$2::uuid AND "
                  "p.permission='transfer' AND p.revoked_at IS NULL AND a.status='active')",
                  actor, account)[0]
      .as<bool>();
}
inline void Account(pqxx::work& tx, const Request& r, const std::string& id,
                    const std::string& permission = "exchange.trade", bool trading = true) {
  Id(id);
  if (r.resource_scoped() &&
      std::find(r.allowed_account_ids().begin(), r.allowed_account_ids().end(), id) == r.allowed_account_ids().end())
    throw DomainError(ErrorCode::kDenied, "account is outside API key scope");
  const auto a = sql::Exec(tx,
                           "SELECT COALESCE(owner_principal_id::text,''),COALESCE(organization_id::text,''),status "
                           "FROM finance.accounts WHERE id=$1::uuid AND account_type IN ('personal','organization')",
                           id);
  if (a.empty()) Fail("personal or organization account required");
  if (!a[0][1].as<std::string>().empty())
    Require(tx, r.context().actor_id(), a[0][1].c_str(), permission);
  else if (a[0][0].as<std::string>() != r.context().actor_id())
    throw DomainError(ErrorCode::kDenied, "an owned trading account is required");
  if (trading && (a[0][2].as<std::string>() != "active" || !Has(tx, r.context().actor_id(), id)))
    throw DomainError(ErrorCode::kDenied, "active transfer mandate required");
  if (trading && permission == "exchange.trade" &&
      sql::Exec(tx,
                "SELECT id FROM finance.account_permissions WHERE account_id=$1::uuid AND principal_id=$2::uuid AND "
                "permission='exchange.trade' AND revoked_at IS NULL FOR SHARE",
                id, r.context().actor_id())
          .empty())
    throw DomainError(ErrorCode::kDenied, "exchange trading mandate is revoked");
}
inline void Issuer(pqxx::work& tx, const Request& r, const std::string& stock,
                   const std::string& permission = "exchange.issuer.manage") {
  const auto org = sql::Exec(tx, "SELECT organization_id::text FROM finance.exchange_stocks WHERE id=$1::uuid", stock);
  if (org.empty()) throw DomainError(ErrorCode::kNotFound, "issuer not found");
  Require(tx, r.context().actor_id(), org[0][0].c_str(), permission);
}
inline Money Cost(Money price, std::int64_t quantity) {
  if (quantity <= 0 || quantity > 1000000000 || price.cents() <= 0 || price.cents() > Money::kMaxCents / quantity)
    throw std::invalid_argument("invalid quantity or notional overflow");
  return Money::FromCents(price.cents() * quantity);
}
inline void Notice(pqxx::work& tx, const std::string& user, const std::string& type, const std::string& data = "{}") {
  sql::Exec(tx,
            "INSERT INTO finance.event_outbox(event_key,principal_id,category,payload) "
            "VALUES('exchange:'||gen_random_uuid(),$1::uuid,'organizations',jsonb_build_object('type','exchange.'||$2::"
            "text,'date',clock_timestamp())||$3::jsonb)",
            user, type, data);
}
inline OperationResult Pay(pqxx::work& tx, const std::string& actor, const std::string& source, const std::string& dest,
                           Money amount, const std::string& key, const std::string& reason) {
  TransferCommand c;
  c.actor_id = actor;
  c.source_account_id = source;
  c.destination_account_id = dest;
  c.amount = amount;
  c.idempotency_key = "exchange:" + key;
  c.request_id = c.idempotency_key;
  c.correlation_id = key;
  c.reason = reason;
  c.fingerprint = sql::One(tx, "SELECT encode(sha256(convert_to($1,'UTF8')),'hex')", key)[0].as<std::string>();
  return PostgresLedger::ExchangeTransfer(tx, c);
}
inline std::string Order(pqxx::work& tx, const std::string& id) {
  return sql::One(tx,
                  "SELECT (to_jsonb(o)||jsonb_build_object('price',price::text,'reserved_cash',COALESCE((SELECT "
                  "amount::text FROM finance.exchange_reserves WHERE order_id=o.id),'0.00'),'fee','0.00'))::text FROM "
                  "finance.exchange_orders o WHERE id=$1::uuid",
                  id)[0]
      .as<std::string>();
}
inline std::string Stock(pqxx::work& tx, const std::string& id) {
  return sql::
      One(tx,
          R"SQL(SELECT (to_jsonb(s)||jsonb_build_object('last_price',(SELECT price::text FROM finance.exchange_trades WHERE stock_id=s.id ORDER BY sequence DESC LIMIT 1),'last_trade_at',(SELECT created_at FROM finance.exchange_trades WHERE stock_id=s.id ORDER BY sequence DESC LIMIT 1),'ipo',(SELECT to_jsonb(i)||jsonb_build_object('price',price::text) FROM finance.exchange_ipos i WHERE stock_id=s.id)))::text FROM finance.exchange_stocks s WHERE id=$1::uuid)SQL",
          id)[0]
          .as<std::string>();
}
inline std::string Dividend(pqxx::work& tx, const std::string& id) {
  return sql::
      One(tx,
          R"SQL(SELECT (to_jsonb(d)||jsonb_build_object('amount',amount::text,'total',total::text,'rounding_remainder',rounding_remainder::text,'items',COALESCE((SELECT jsonb_agg(to_jsonb(i)||jsonb_build_object('amount',amount::text) ORDER BY i.id) FROM finance.exchange_distributions i WHERE dividend_id=d.id),'[]'::jsonb)))::text FROM finance.exchange_dividends d WHERE id=$1::uuid)SQL",
          id)[0]
          .as<std::string>();
}
inline std::string Page(const pqxx::result& rows, int limit) {
  std::string out = "{\"items\":[";
  int n = 0;
  for (const auto& row : rows) {
    if (n == limit) break;
    if (n++) out += ',';
    out += row[1].as<std::string>();
  }
  out += "],\"next_cursor\":";
  if (rows.size() > static_cast<pqxx::result::size_type>(limit)) {
    out += '"';
    out += rows[limit - 1][0].as<std::string>();
    out += '"';
  } else {
    out += "null";
  }
  out += '}';
  return out;
}
void Reserve(pqxx::work&, const std::string&, Money);
void Cancel(pqxx::work&, const std::string&, const std::string&);
void Shares(pqxx::work&, const std::string&, const std::string&, const std::string&, std::int64_t, Money,
            const std::string&);
std::string SubmitOrder(pqxx::work&, const Request&);
void Expire(pqxx::work&);
std::string Read(pqxx::work&, const Request&);
std::string Corporate(pqxx::work&, const Request&);
}  // namespace horizon::exchange

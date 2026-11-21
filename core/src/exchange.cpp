#include "horizon/exchange.hpp"

#include "horizon/exchange_internal.hpp"

namespace horizon {
std::string Exchange::Execute(const std::string& database, const banking::v1::BusinessRequest& r) {
  using namespace exchange;
  const auto actor = r.context().actor_id(), action = r.action(), data = r.payload().empty() ? "{}" : r.payload();
  if (data.size() > 65536) throw std::invalid_argument("exchange payload too large");
  Id(actor);
  if (!r.target_id().empty()) Id(r.target_id());
  pqxx::connection db{database};
  pqxx::work tx{db};
  sql::Exec(tx, "SELECT pg_advisory_xact_lock(250025)");
  if (action.rfind("read.", 0) == 0) {
    const auto out = Read(tx, r);
    tx.commit();
    return out;
  }
  if (action == "maintenance") {
    if (r.context().source() != "exchange-worker") throw DomainError(ErrorCode::kDenied, "exchange worker required");
    Expire(tx);
    tx.commit();
    return "{}";
  }
  const auto key = Field(tx, data, "key");
  if (key.size() < 16 || key.size() > 255) throw std::invalid_argument("stable idempotency key required");
  const auto hash =
      sql::One(tx,
               "SELECT encode(sha256(convert_to(jsonb_build_array($1::jsonb,$2::text,$3::text)::text,'UTF8')),'hex')",
               data, r.target_id(), r.organization_id())[0]
          .as<std::string>();
  const auto old = sql::Exec(tx,
                             "SELECT fingerprint,response::text FROM finance.exchange_requests WHERE actor_id=$1::uuid "
                             "AND action=$2 AND key=$3",
                             actor, action, key);
  if (!old.empty()) {
    if (old[0][0].as<std::string>() != hash) Fail("idempotency payload mismatch");
    if (r.resource_scoped()) {
      const auto account =
          action == "order.create" ? Field(tx, data, "account_id") : Field(tx, old[0][1].c_str(), "account_id");
      if (std::find(r.allowed_account_ids().begin(), r.allowed_account_ids().end(), account) ==
          r.allowed_account_ids().end())
        throw DomainError(ErrorCode::kDenied, "replay account is outside API key scope");
    }
    return old[0][1].as<std::string>();
  }
  try {
    Expire(tx);
    std::string out;
    if (action == "order.create")
      out = SubmitOrder(tx, r);
    else if (action == "order.cancel") {
      const auto found =
          sql::Exec(tx, "SELECT account_id::text FROM finance.exchange_orders WHERE id=$1::uuid AND actor_id=$2::uuid",
                    r.target_id(), actor);
      if (found.empty()) throw DomainError(ErrorCode::kNotFound, "order not found");
      // Owners may release their own reserves even after a trading permission is revoked.
      if (r.resource_scoped() && std::find(r.allowed_account_ids().begin(), r.allowed_account_ids().end(),
                                           found[0][0].as<std::string>()) == r.allowed_account_ids().end())
        throw DomainError(ErrorCode::kDenied, "account outside API key");
      Cancel(tx, r.target_id(), "cancelled");
      out = Order(tx, r.target_id());
    } else
      out = Corporate(tx, r);
    sql::Exec(tx,
              "INSERT INTO finance.exchange_requests(actor_id,action,key,fingerprint,response) "
              "VALUES($1::uuid,$2,$3,$4,$5::jsonb)",
              actor, action, key, hash, out);
    tx.commit();
    return out;
  } catch (const DomainError& error) {
    tx.abort();
    pqxx::work rejected{db};
    Audit(rejected, actor, "exchange.rejected", r.target_id().empty() ? actor : r.target_id(),
          sql::One(rejected, "SELECT jsonb_build_object('action',$1::text,'reason',$2::text)::text", action,
                   std::string(error.what()).substr(0, 500))[0]
              .as<std::string>());
    rejected.commit();
    throw;
  }
}
}  // namespace horizon

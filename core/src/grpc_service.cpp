#include "horizon/account_directory.hpp"
#include "horizon/administration.hpp"
#include "horizon/atm.hpp"
#include "horizon/business.hpp"
#include "horizon/error.hpp"
#include "horizon/exchange.hpp"
#include "horizon/hpay.hpp"
#include "horizon/payment_requests.hpp"
#include "horizon/postgres_ledger.hpp"
#include "horizon/privacy.hpp"
#include "horizon/sql.hpp"
// Limit a gRPC 1.81 public-header deprecation warning to vendor includes.
#ifdef _MSC_VER
#pragma warning(push)
#pragma warning(disable : 4996)
#endif
#include <grpcpp/server_context.h>

#include "horizon/banking/v1/banking.grpc.pb.h"
#ifdef _MSC_VER
#pragma warning(pop)
#endif
#include <algorithm>
#include <memory>
#include <pqxx/pqxx>
#include <regex>

namespace horizon {
namespace pb = ::horizon::banking::v1;
namespace {
grpc::Status Failure(const std::exception& error) {
  if (const auto* domain = dynamic_cast<const DomainError*>(&error)) {
    switch (domain->code()) {
      case ErrorCode::kDenied:
        return {grpc::StatusCode::PERMISSION_DENIED, domain->what()};
      case ErrorCode::kNotFound:
        return {grpc::StatusCode::NOT_FOUND, domain->what()};
      case ErrorCode::kConflict:
        return {grpc::StatusCode::ALREADY_EXISTS, domain->what()};
      case ErrorCode::kInsufficient:
        return {grpc::StatusCode::FAILED_PRECONDITION, domain->what()};
      case ErrorCode::kLimit:
        return {grpc::StatusCode::RESOURCE_EXHAUSTED, domain->what()};
      default:
        return {grpc::StatusCode::INVALID_ARGUMENT, domain->what()};
    }
  }
  if (dynamic_cast<const pqxx::broken_connection*>(&error))
    return {grpc::StatusCode::UNAVAILABLE, "financial database unavailable"};
  if (dynamic_cast<const pqxx::sql_error*>(&error))
    return {grpc::StatusCode::INTERNAL, "financial database operation failed"};
  if (dynamic_cast<const std::invalid_argument*>(&error) || dynamic_cast<const std::out_of_range*>(&error))
    return {grpc::StatusCode::INVALID_ARGUMENT, "invalid financial request"};
  return {grpc::StatusCode::INTERNAL, "financial operation failed"};
}
void Uuid(const std::string& value) {
  static const std::regex pattern("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$");
  if (!std::regex_match(value, pattern)) throw std::invalid_argument("canonical UUID required");
}
Money ReadMoney(const pb::Money& value) {
  if (value.currency() != "DB") throw std::invalid_argument("unsupported currency");
  return Money::Parse(value.amount());
}
void WriteMoney(Money value, pb::Money* out) {
  out->set_amount(value.ToString());
  out->set_currency("DB");
}
void WriteAccount(const pqxx::row& row, pb::Account* out) {
  out->set_id(row[0].c_str());
  out->set_status(row[1].c_str());
  out->set_account_type(row[2].c_str());
  const auto balance = Money::Parse(row[3].c_str());
  const auto held = Money::Parse(row[4].c_str());
  WriteMoney(balance, out->mutable_balance());
  WriteMoney(held, out->mutable_held());
  WriteMoney(balance - held, out->mutable_available());
}
void WriteOperation(const OperationResult& value, pb::Operation* out) {
  out->set_id(value.id);
  out->set_type(value.operation_type);
  out->set_status(value.status);
  out->set_source_account_id(value.source_account_id);
  out->set_destination_account_id(value.destination_account_id);
  WriteMoney(value.amount, out->mutable_amount());
  out->set_created_at(value.created_at);
  out->set_completed_at(value.completed_at);
  out->set_related_operation_id(value.related_operation_id);
  out->set_hold_id(value.hold_id);
  out->mutable_fee()->set_amount("0.00");
  out->mutable_fee()->set_currency("DB");
}
constexpr const char* kOperationSelect =
    "SELECT o.id::text,o.operation_type,o.status,COALESCE(o.source_account_id::text,''),"
    "COALESCE(o.destination_account_id::text,''),o.amount::text,o.created_at::text,"
    "COALESCE(o.completed_at::text,''),COALESCE(o.related_operation_id::text,''),COALESCE(h.id::text,'') "
    "FROM finance.financial_operations o LEFT JOIN finance.holds h ON h.operation_id=o.id ";
OperationResult OperationFromRow(const pqxx::row& row) {
  return {row[0].c_str(), row[1].c_str(), row[2].c_str(),
          row[3].c_str(), row[4].c_str(), row[6].c_str(),
          row[7].c_str(), row[8].c_str(), Money::Parse(row[5].c_str()),
          row[9].c_str()};
}

class BankingGrpcService final : public pb::BankingService::Service {
 public:
  BankingGrpcService(std::string database, std::string token, std::string issuance)
      : database_(std::move(database)), token_(std::move(token)), issuance_(std::move(issuance)), ledger_(database_) {}

  grpc::Status Privacy(grpc::ServerContext* c, const pb::BusinessRequest* r, pb::JsonResult* out) override {
    return Call(c, r->context(), [&] { out->set_json(horizon::Privacy::Execute(database_, *r)); });
  }
  grpc::Status Atm(grpc::ServerContext* c, const pb::BusinessRequest* r, pb::JsonResult* out) override {
    return Call(c, r->context(), [&] { out->set_json(horizon::Atm::Execute(database_, *r)); });
  }
  grpc::Status Exchange(grpc::ServerContext* c, const pb::BusinessRequest* r, pb::JsonResult* out) override {
    return Call(c, r->context(), [&] { out->set_json(horizon::Exchange::Execute(database_, *r)); });
  }
  grpc::Status Business(grpc::ServerContext* c, const pb::BusinessRequest* r, pb::JsonResult* out) override {
    return Call(c, r->context(), [&] {
      out->set_json(r->action().starts_with("hpay.") ? hpay::Catalogue(database_, *r)
                                                     : horizon::Business::Execute(database_, *r));
    });
  }

  grpc::Status PaymentRequests(grpc::ServerContext* c, const pb::PaymentRequestCommand* r,
                               pb::JsonResult* out) override {
    return Call(c, r->context(), [&] { out->set_json(horizon::PaymentRequests::Execute(database_, *r)); });
  }

  grpc::Status AdministrativeQuery(grpc::ServerContext* c, const pb::AdminQueryRequest* r,
                                   pb::JsonResult* out) override {
    return Call(c, r->context(), [&] { out->set_json(Administration::Query(database_, *r)); });
  }
  grpc::Status AdministrativeChange(grpc::ServerContext* c, const pb::AdminChangeRequest* r,
                                    pb::JsonResult* out) override {
    return Call(c, r->context(), [&] { out->set_json(Administration::Change(database_, *r)); });
  }
  grpc::Status NotificationEvents(grpc::ServerContext* c, const pb::NotificationEventsRequest* r,
                                  pb::JsonResult* out) override {
    return Call(c, r->context(), [&] {
      if (r->context().source() != "notification-relay")
        throw DomainError(ErrorCode::kDenied, "relay context required");
      out->set_json(Administration::Events(database_));
    });
  }
  grpc::Status AcknowledgeNotificationEvents(grpc::ServerContext* c, const pb::NotificationEventsRequest* r,
                                             pb::JsonResult* out) override {
    return Call(c, r->context(), [&] {
      if (r->context().source() != "notification-relay")
        throw DomainError(ErrorCode::kDenied, "relay context required");
      Administration::Acknowledge(database_, *r);
      out->set_json("{}");
    });
  }

  grpc::Status CreateAccount(grpc::ServerContext* c, const pb::CreateAccountRequest* r, pb::Account* out) override {
    return Call(c, r->context(), [&] { AccountDirectory(database_).Create(*r, out); });
  }
  grpc::Status ListAccounts(grpc::ServerContext* c, const pb::ListAccountsRequest* r, pb::AccountPage* out) override {
    return Call(c, r->context(), [&] {
      if (!r->cursor().empty()) Uuid(r->cursor());
      const auto limit = static_cast<pqxx::result::size_type>(std::clamp(r->limit(), 1U, 100U));
      pqxx::connection db{database_};
      pqxx::read_transaction tx{db};
      const auto rows = sql::Exec(
          tx,
          "SELECT a.id::text,a.status,a.account_type,a.balance::text,a.held::text FROM finance.accounts a "
          "WHERE a.account_type<>'system' AND ($2='' OR a.id>NULLIF($2,'')::uuid) AND EXISTS(SELECT 1 FROM "
          "finance.account_permissions p "
          "WHERE p.account_id=a.id AND p.principal_id=$1::uuid AND p.permission='accounts.read' AND "
          "p.revoked_at IS NULL) AND EXISTS(SELECT 1 FROM finance.account_permissions b WHERE b.account_id=a.id AND "
          "b.principal_id=$1::uuid AND b.permission='balance.read' AND b.revoked_at IS NULL) "
          "ORDER BY a.id LIMIT $3",
          r->context().actor_id(), r->cursor(), limit + 1);
      for (pqxx::result::size_type i = 0; i < rows.size() && i < limit; ++i) {
        auto* account = out->add_accounts();
        WriteAccount(rows[i], account);
        AccountDirectory::Enrich(tx, r->context().actor_id(), account);
      }
      if (rows.size() > limit) out->set_next_cursor(rows[limit - 1][0].c_str());
    });
  }
  grpc::Status GetAccount(grpc::ServerContext* c, const pb::GetAccountRequest* r, pb::Account* out) override {
    return Account(c, r->context(), r->account_id(), out, "accounts.read");
  }
  grpc::Status GetBalance(grpc::ServerContext* c, const pb::GetBalanceRequest* r, pb::Account* out) override {
    return Account(c, r->context(), r->account_id(), out, "balance.read");
  }
  grpc::Status Transfer(grpc::ServerContext* c, const pb::OperationRequest* r, pb::Operation* out) override {
    return TransferAs(c, *r, out, "transfer");
  }
  grpc::Status CreatePayment(grpc::ServerContext* c, const pb::OperationRequest* r, pb::Operation* out) override {
    return TransferAs(c, *r, out, "payment");
  }
  grpc::Status Issue(grpc::ServerContext* c, const pb::OperationRequest* r, pb::Operation* out) override {
    return Call(c, r->context(), [&] {
      auto cmd = Command(*r, "issue");
      cmd.source_account_id = issuance_;
      Uuid(cmd.destination_account_id);
      WriteOperation(ledger_.Transfer(cmd), out);
    });
  }
  grpc::Status Refund(grpc::ServerContext* c, const pb::OperationRequest* r, pb::Operation* out) override {
    return Call(c, r->context(), [&] {
      Uuid(r->related_operation_id());
      Uuid(r->source_account_id());
      WriteOperation(ledger_.Refund(Command(*r, "refund"), r->related_operation_id()), out);
    });
  }
  grpc::Status CreateHold(grpc::ServerContext* c, const pb::HoldRequest* r, pb::Operation* out) override {
    return Call(c, r->context(), [&] {
      Uuid(r->account_id());
      TransferCommand cmd{r->context().actor_id(),
                          r->account_id(),
                          "",
                          r->idempotency_key(),
                          r->request_fingerprint(),
                          r->reason(),
                          "",
                          r->context().request_id(),
                          r->context().correlation_id(),
                          "hold",
                          ReadMoney(r->amount()),
                          r->context().source()};
      WriteOperation(ledger_.CreateHold(cmd), out);
    });
  }
  grpc::Status ReleaseHold(grpc::ServerContext* c, const pb::HoldActionRequest* r, pb::Operation* out) override {
    return HoldAction(c, *r, out, false);
  }
  grpc::Status CaptureHold(grpc::ServerContext* c, const pb::HoldActionRequest* r, pb::Operation* out) override {
    return HoldAction(c, *r, out, true);
  }
  grpc::Status GetOperation(grpc::ServerContext* c, const pb::GetOperationRequest* r, pb::Operation* out) override {
    return Call(c, r->context(), [&] {
      Uuid(r->operation_id());
      pqxx::connection db{database_};
      pqxx::read_transaction tx{db};
      const auto rows = sql::Exec(tx, std::string(kOperationSelect) + "WHERE o.id=$1::uuid", r->operation_id());
      if (rows.empty()) throw DomainError(ErrorCode::kNotFound, "operation not found");
      const auto result = OperationFromRow(rows[0]);
      if (!Allowed(tx, r->context().actor_id(), result.source_account_id, "history.read") &&
          !Allowed(tx, r->context().actor_id(), result.destination_account_id, "history.read"))
        throw DomainError(ErrorCode::kDenied, "operation access is not granted");
      WriteOperation(result, out);
      AccountDirectory::EnrichOperation(tx, out);
    });
  }
  grpc::Status GetHistory(grpc::ServerContext* c, const pb::HistoryRequest* r, pb::OperationHistory* out) override {
    return Call(c, r->context(), [&] {
      Uuid(r->account_id());
      if (!r->cursor().empty()) Uuid(r->cursor());
      pqxx::connection db{database_};
      pqxx::read_transaction tx{db};
      if (!Allowed(tx, r->context().actor_id(), r->account_id(), "history.read"))
        throw DomainError(ErrorCode::kDenied, "history access is not granted");
      if (!r->cursor().empty() && sql::Exec(tx,
                                            "SELECT id FROM finance.financial_operations WHERE id=$1::uuid "
                                            "AND (source_account_id=$2::uuid OR destination_account_id=$2::uuid)",
                                            r->cursor(), r->account_id())
                                      .empty())
        throw std::invalid_argument("invalid history cursor");
      const auto limit = static_cast<pqxx::result::size_type>(std::clamp(r->limit(), 1U, 100U));
      const auto rows =
          sql::Exec(tx,
                    std::string(kOperationSelect) +
                        "WHERE (o.source_account_id=$1::uuid OR o.destination_account_id=$1::uuid) AND ($2='' OR "
                        "(o.created_at,o.id)<"
                        "(SELECT created_at,id FROM finance.financial_operations WHERE id=NULLIF($2,'')::uuid)) "
                        "ORDER BY o.created_at DESC,o.id DESC LIMIT $3",
                    r->account_id(), r->cursor(), limit + 1);
      for (pqxx::result::size_type i = 0; i < rows.size() && i < limit; ++i) {
        auto* operation = out->add_operations();
        WriteOperation(OperationFromRow(rows[i]), operation);
        AccountDirectory::EnrichOperation(tx, operation);
      }
      if (rows.size() > limit) out->set_next_cursor(rows[limit - 1][0].c_str());
    });
  }
  grpc::Status CheckAuthorization(grpc::ServerContext* c, const pb::AuthorizationRequest* r,
                                  pb::AuthorizationDecision* out) override {
    return Call(c, r->context(), [&] {
      Uuid(r->account_id());
      pqxx::connection db{database_};
      pqxx::read_transaction tx{db};
      const bool allowed = Allowed(tx, r->context().actor_id(), r->account_id(), r->permission());
      out->set_allowed(allowed);
      out->set_reason_code(allowed ? "allowed" : "not_granted");
    });
  }
  grpc::Status EnsurePrimaryAccount(grpc::ServerContext* c, const pb::PrimaryAccountRequest* r,
                                    pb::Account* out) override {
    return Call(c, r->context(), [&] { AccountDirectory(database_).EnsurePrimary(r->context().actor_id(), out); });
  }
  grpc::Status GetRequestOutcome(grpc::ServerContext* c, const pb::RequestOutcomeRequest* r,
                                 pb::Operation* out) override {
    return Call(c, r->context(), [&] {
      pqxx::connection db{database_};
      pqxx::read_transaction tx{db};
      sql::Exec(tx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                r->context().actor_id() + ":" + r->operation_type() + ":" + r->idempotency_key());
      const auto rows =
          sql::Exec(tx,
                    std::string(kOperationSelect) +
                        "JOIN finance.idempotency_records i ON i.operation_id=o.id WHERE i.scope_id=$1::uuid AND "
                        "i.operation_type=$2 AND i.idempotency_key=$3 AND i.state='completed'",
                    r->context().actor_id(), r->operation_type(), r->idempotency_key());
      if (rows.empty()) throw DomainError(ErrorCode::kNotFound, "request outcome not found");
      WriteOperation(OperationFromRow(rows[0]), out);
      AccountDirectory::EnrichOperation(tx, out);
    });
  }
  grpc::Status UpdateAccount(grpc::ServerContext* c, const pb::UpdateAccountRequest* r, pb::Account* out) override {
    return Call(c, r->context(), [&] {
      Uuid(r->account_id());
      AccountDirectory(database_).Update(*r, out);
    });
  }
  grpc::Status ResolveAccount(grpc::ServerContext* c, const pb::PublicAccountRequest* r,
                              pb::PublicAccount* out) override {
    return Call(c, r->context(), [&] {
      if (!r->owner_id().empty()) Uuid(r->owner_id());
      if (r->identifier().size() > 80) throw std::invalid_argument("invalid account identifier");
      AccountDirectory(database_).Resolve(*r, out);
    });
  }
  grpc::Status EnsureOrganization(grpc::ServerContext* c, const pb::OrganizationRequest* r,
                                  pb::OrganizationView* out) override {
    return Call(c, r->context(), [&] {
      Uuid(r->organization_id());
      Uuid(r->owner_id());
      for (const auto& member : r->members()) Uuid(member.user_id());
      AccountDirectory(database_).EnsureOrganization(*r, out);
    });
  }
  grpc::Status GetOrganization(grpc::ServerContext* c, const pb::OrganizationRequest* r,
                               pb::OrganizationView* out) override {
    return Call(c, r->context(), [&] {
      Uuid(r->organization_id());
      AccountDirectory(database_).Organization(*r, out);
    });
  }
  grpc::Status CreateOrganizationAccount(grpc::ServerContext* c, const pb::OrganizationAccountRequest* r,
                                         pb::Account* out) override {
    return Call(c, r->context(), [&] {
      Uuid(r->organization_id());
      AccountDirectory(database_).OrganizationAccount(*r, out);
    });
  }
  grpc::Status ChangeOrganizationMember(grpc::ServerContext* c, const pb::OrganizationMemberRequest* r,
                                        pb::OrganizationView* out) override {
    return Call(c, r->context(), [&] {
      Uuid(r->organization_id());
      Uuid(r->member().user_id());
      AccountDirectory(database_).ChangeMember(*r, out);
    });
  }
  grpc::Status GetActivity(grpc::ServerContext* c, const pb::ActivityRequest* r, pb::OperationHistory* out) override {
    return Call(c, r->context(), [&] {
      if (!r->account_id().empty()) Uuid(r->account_id());
      if (!r->cursor().empty()) Uuid(r->cursor());
      if (r->query().size() > 400 || r->from_date().size() > 40 || r->to_date().size() > 40)
        throw std::invalid_argument("invalid activity filter");
      const bool ascending = r->order() == "asc";
      if (!r->order().empty() && r->order() != "asc" && r->order() != "desc")
        throw std::invalid_argument("invalid activity ordering");
      pqxx::connection db{database_};
      pqxx::read_transaction tx{db};
      const auto limit = static_cast<pqxx::result::size_type>(std::clamp(r->limit(), 1U, 100U));
      const std::string access =
          "EXISTS(SELECT 1 FROM finance.account_permissions p WHERE p.principal_id=$1::uuid AND "
          "p.permission='history.read' AND p.revoked_at IS NULL AND (p.account_id=o.source_account_id OR "
          "p.account_id=o.destination_account_id))";
      if (!r->cursor().empty() &&
          sql::Exec(tx, "SELECT o.id FROM finance.financial_operations o WHERE o.id=$2::uuid AND " + access,
                    r->context().actor_id(), r->cursor())
              .empty())
        throw std::invalid_argument("invalid activity cursor");
      const std::string comparator = ascending ? ">" : "<", direction = ascending ? "ASC" : "DESC";
      const auto rows = sql::Exec(
          tx,
          std::string(kOperationSelect) + "WHERE " + access +
              " AND ($2='' OR o.source_account_id=NULLIF($2,'')::uuid OR o.destination_account_id=NULLIF($2,'')::uuid)"
              " AND ($3='' OR (o.created_at,o.id)" +
              comparator +
              "(SELECT created_at,id FROM finance.financial_operations WHERE id=NULLIF($3,'')::uuid))"
              " AND ($4='' OR o.operation_type=$4) AND ($5='' OR position(lower($5) in lower(o.reason))>0 OR "
              "o.id::text=$5)"
              " AND ($6='' OR o.created_at>=NULLIF($6,'')::timestamptz) AND ($7='' OR "
              "o.created_at<NULLIF($7,'')::timestamptz)"
              " ORDER BY o.created_at " +
              direction + ",o.id " + direction + " LIMIT $8",
          r->context().actor_id(), r->account_id(), r->cursor(), r->operation_type(), r->query(), r->from_date(),
          r->to_date(), limit + 1);
      for (pqxx::result::size_type i = 0; i < rows.size() && i < limit; ++i) {
        auto* operation = out->add_operations();
        WriteOperation(OperationFromRow(rows[i]), operation);
        AccountDirectory::EnrichOperation(tx, operation);
      }
      if (rows.size() > limit) out->set_next_cursor(rows[limit - 1][0].c_str());
    });
  }

 private:
  template <class F>
  grpc::Status Call(grpc::ServerContext* c, const pb::RequestContext& ctx, F action) {
    const auto token = c->client_metadata().find("x-horizon-internal-token");
    if (token == c->client_metadata().end() || std::string(token->second.data(), token->second.length()) != token_)
      return {grpc::StatusCode::UNAUTHENTICATED, "internal service authentication required"};
    try {
      Uuid(ctx.actor_id());
      if (ctx.request_id().empty() || ctx.request_id().size() > 100 || ctx.correlation_id().size() > 100)
        throw std::invalid_argument("invalid request context");
      if (c->IsCancelled()) return {grpc::StatusCode::CANCELLED, "request cancelled"};
      {
        pqxx::connection db{database_};
        pqxx::read_transaction tx{db};
        // Keep an actor fence until the financial transaction has finished.
        // Erasure takes the corresponding exclusive lock before revoking grants.
        sql::Exec(tx, "SELECT pg_advisory_xact_lock_shared(hashtextextended($1,0))", "privacy:" + ctx.actor_id());
        if (!sql::Exec(tx, "SELECT user_id FROM finance.privacy_subjects WHERE user_id=$1::uuid", ctx.actor_id())
                 .empty())
          throw DomainError(ErrorCode::kDenied, "profile processing is restricted");
        action();
      }
      return grpc::Status::OK;
    } catch (const std::exception& error) {
      return Failure(error);
    }
  }
  bool Allowed(pqxx::transaction_base& tx, const std::string& actor, const std::string& account,
               const std::string& permission) {
    if (account.empty()) return false;
    return sql::One(tx,
                    "SELECT EXISTS(SELECT 1 FROM finance.account_permissions WHERE account_id=$1::uuid "
                    "AND principal_id=$2::uuid AND permission=$3 AND revoked_at IS NULL)",
                    account, actor, permission)[0]
        .as<bool>();
  }
  TransferCommand Command(const pb::OperationRequest& r, const std::string& type) {
    return {r.context().actor_id(),
            r.source_account_id(),
            r.destination_account_id(),
            r.idempotency_key(),
            r.request_fingerprint(),
            r.reason(),
            r.authority_reference(),
            r.context().request_id(),
            r.context().correlation_id(),
            type,
            ReadMoney(r.amount()),
            r.context().source(),
            r.not_after()};
  }
  grpc::Status TransferAs(grpc::ServerContext* c, const pb::OperationRequest& r, pb::Operation* out,
                          const std::string& type) {
    return Call(c, r.context(), [&] {
      Uuid(r.source_account_id());
      Uuid(r.destination_account_id());
      WriteOperation(ledger_.Transfer(Command(r, type)), out);
    });
  }
  grpc::Status HoldAction(grpc::ServerContext* c, const pb::HoldActionRequest& r, pb::Operation* out, bool capture) {
    return Call(c, r.context(), [&] {
      Uuid(r.hold_id());
      if (capture) Uuid(r.destination_account_id());
      TransferCommand cmd{r.context().actor_id(),
                          "",
                          r.destination_account_id(),
                          r.idempotency_key(),
                          r.request_fingerprint(),
                          r.reason(),
                          "",
                          r.context().request_id(),
                          r.context().correlation_id(),
                          capture ? "capture_hold" : "release_hold",
                          Money{},
                          r.context().source()};
      WriteOperation(ledger_.ChangeHold(cmd, r.hold_id(), capture), out);
    });
  }
  grpc::Status Account(grpc::ServerContext* c, const pb::RequestContext& ctx, const std::string& id, pb::Account* out,
                       const std::string& permission) {
    return Call(c, ctx, [&] {
      Uuid(id);
      pqxx::connection db{database_};
      pqxx::read_transaction tx{db};
      if (!Allowed(tx, ctx.actor_id(), id, permission))
        throw DomainError(ErrorCode::kDenied, "account access is not granted");
      if (!Allowed(tx, ctx.actor_id(), id, "balance.read"))
        throw DomainError(ErrorCode::kDenied, "balance access is not granted");
      const auto rows = sql::Exec(tx,
                                  "SELECT id::text,status,account_type,balance::text,held::text FROM finance.accounts "
                                  "WHERE id=$1::uuid AND account_type<>'system'",
                                  id);
      if (rows.empty()) throw DomainError(ErrorCode::kNotFound, "account not found");
      WriteAccount(rows[0], out);
      AccountDirectory::Enrich(tx, ctx.actor_id(), out);
    });
  }
  std::string database_, token_, issuance_;
  PostgresLedger ledger_;
};
}  // namespace
std::unique_ptr<pb::BankingService::Service> CreateGrpcService(const std::string& database, const std::string& token,
                                                               const std::string& issuance) {
  return std::make_unique<BankingGrpcService>(database, token, issuance);
}
}  // namespace horizon

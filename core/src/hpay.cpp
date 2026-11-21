#include "horizon/hpay.hpp"

#include "horizon/business_internal.hpp"
#include "horizon/postgres_ledger.hpp"

namespace horizon::hpay {
namespace {
using Request = banking::v1::BusinessRequest;

void CheckProductId(const std::string& id) {
  static const std::regex digits("[1-9][0-9]{0,18}");
  if (!std::regex_match(id, digits) || (id.size() == 19 && id > "9223372036854775807"))
    throw std::invalid_argument("invalid HPay product identifier");
}

void RequireManager(pqxx::work& tx, const std::string& org, const std::string& actor) {
  if (sql::Exec(tx,
                "SELECT 1 FROM finance.organization_members WHERE organization_id=$1::uuid "
                "AND principal_id=$2::uuid AND revoked_at IS NULL AND role IN ('owner','administrator') FOR SHARE",
                org, actor)
          .empty())
    throw DomainError(ErrorCode::kDenied, "HPay requires an organization manager");
}

bool ReceivingAccount(pqxx::work& tx, const std::string& org, const std::string& account) {
  return !sql::Exec(tx,
                    "SELECT 1 FROM finance.accounts WHERE id=$1::uuid AND organization_id=$2::uuid "
                    "AND account_type='organization' AND status='active'",
                    account, org)
              .empty();
}

std::string Receipt(pqxx::work& tx, const std::string& id) {
  return sql::One(tx,
                  "SELECT (to_jsonb(p)-ARRAY['buyer_id','account_id','organization_id','protected_until'] "
                  "||jsonb_build_object('product_id',product_id::text,'price',price::text))::text "
                  "FROM finance.hpay_purchases p WHERE id=$1::uuid",
                  id)[0]
      .as<std::string>();
}
}  // namespace

std::string Catalogue(const std::string& database, const Request& r) {
  const auto org = r.organization_id(), actor = r.context().actor_id(), action = r.action();
  business::Id(org);
  if (r.resource_scoped() || r.context().source() != "rest")
    throw DomainError(ErrorCode::kDenied, "interactive organization session required");
  pqxx::connection db{database};
  pqxx::work tx{db};
  RequireManager(tx, org, actor);

  auto id = r.target_id();
  if (!id.empty()) CheckProductId(id);
  if (action == "hpay.sales") {
    return sql::One(tx, R"SQL(
      SELECT jsonb_build_object(
        'count',count(*)::text,'total',COALESCE(sum(price),0)::numeric(30,2)::text,
        'items',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',s.id,'product_id',s.product_id::text,
          'product_name',s.product_name,'price',s.price::text,'completed_at',s.completed_at,
          'operation_id',s.operation_id) ORDER BY s.completed_at DESC,s.id) FROM (
            SELECT * FROM finance.hpay_purchases WHERE organization_id=$1::uuid AND status='completed'
            ORDER BY completed_at DESC,id LIMIT 100 OFFSET $2::integer
          ) s),'[]'::jsonb))::text
      FROM finance.hpay_purchases WHERE organization_id=$1::uuid AND status='completed'
    )SQL",
                    org, business::Field(tx, r.payload(), "offset", "0"))[0]
        .as<std::string>();
  }
  if (action == "hpay.create" || action == "hpay.update") {
    const auto field = [&](const std::string& key) { return business::Field(tx, r.payload(), key); };
    const auto account = field("account_id"), name = field("name"), description = field("description");
    const auto status = field("status");
    const auto price = Money::Parse(field("price"));
    if (action == "hpay.update" && sql::Exec(tx,
                                             "SELECT 1 FROM finance.hpay_products WHERE id=$1::bigint "
                                             "AND organization_id=$2::uuid AND status<>'archived' FOR UPDATE",
                                             id, org)
                                       .empty())
      throw DomainError(ErrorCode::kNotFound, "HPay product not found or archived");
    business::Id(account);
    if (name.empty() || name.size() > 320 || description.size() > 2000 || price.cents() <= 0 ||
        (status != "active" && status != "disabled" && status != "archived") ||
        (action == "hpay.create" && status == "archived"))
      throw std::invalid_argument("invalid HPay product");
    if (!ReceivingAccount(tx, org, account)) {
      const auto existing = sql::Exec(tx,
                                      "SELECT 1 FROM finance.hpay_products WHERE organization_id=$1::uuid "
                                      "AND id=NULLIF($2,'')::bigint AND account_id=$3::uuid",
                                      org, id, account);
      if (status == "active" || existing.empty())
        throw DomainError(ErrorCode::kConflict, "active organization receiving account required");
    }
    const auto changed =
        action == "hpay.create"
            ? sql::Exec(tx,
                        "INSERT INTO finance.hpay_products(organization_id,account_id,name,description,price,status) "
                        "VALUES($1::uuid,$2::uuid,$3,$4,$5::numeric,$6) RETURNING id::text",
                        org, account, name, description, price.ToString(), status)
            : sql::Exec(tx,
                        "UPDATE finance.hpay_products SET account_id=$2::uuid,name=$3,description=$4,"
                        "price=$5::numeric,status=$6,activation_revision=activation_revision+"
                        "CASE WHEN status='active' AND $6<>'active' THEN 1 ELSE 0 END "
                        "WHERE organization_id=$1::uuid AND id=$7::bigint "
                        "AND status<>'archived' RETURNING id::text",
                        org, account, name, description, price.ToString(), status, id);
    if (changed.empty()) throw DomainError(ErrorCode::kNotFound, "HPay product not found or archived");
    id = changed[0][0].as<std::string>();
    business::Audit(tx, actor, action, org,
                    sql::One(tx, "SELECT jsonb_build_object('product_id',$1::text)::text", id)[0].as<std::string>());
  } else if (action != "hpay.list" && action != "hpay.get") {
    throw std::invalid_argument("unknown HPay catalogue action");
  }

  const auto result = sql::One(tx, R"SQL(
    SELECT jsonb_build_object('items',COALESCE(jsonb_agg(
      (to_jsonb(p)-'activation_revision')||jsonb_build_object('id',p.id::text,'code','H-'||p.id::text,'price',p.price::text,
       'sales_count',sales.count::text,'sales_total',sales.total::text) ORDER BY p.id DESC),'[]'))::text
    FROM (SELECT * FROM finance.hpay_products WHERE organization_id=$1::uuid
          AND ($2='' OR id=NULLIF($2,'')::bigint) ORDER BY id DESC LIMIT 100 OFFSET $3::integer) p
    CROSS JOIN LATERAL (SELECT count(*),COALESCE(sum(price),0)::numeric(30,2) AS total
      FROM finance.hpay_purchases WHERE product_id=p.id AND status='completed') sales
  )SQL",
                               org, id, business::Field(tx, r.payload(), "offset", "0"))[0]
                          .as<std::string>();
  tx.commit();
  return result;
}

std::string Purchase(pqxx::work& tx, const Request& r) {
  const auto field = [&](const std::string& key) { return business::Field(tx, r.payload(), key); };
  const auto buyer = field("user_id");
  business::Id(buyer);
  sql::Exec(tx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "hpay:" + buyer);
  sql::Exec(tx,
            "UPDATE finance.hpay_purchases SET status='expired' WHERE buyer_id=$1::uuid "
            "AND status='pending' AND expires_at<=clock_timestamp()",
            buyer);

  if (r.action() == "hpay.cancel") {
    const auto cancelled = sql::Exec(tx,
                                     "UPDATE finance.hpay_purchases SET status='cancelled' "
                                     "WHERE buyer_id=$1::uuid AND status='pending' RETURNING id::text",
                                     buyer);
    return cancelled.empty() ? R"({"status":"none"})" : Receipt(tx, cancelled[0][0].c_str());
  }
  const auto product = field("product_id");
  CheckProductId(product);

  if (r.action() == "hpay.confirm") {
    const auto id = field("purchase_id");
    business::Id(id);
    const auto purchase = sql::Exec(tx, R"SQL(
      SELECT status,organization_id::text,account_id::text,price::text,expires_at::text,
             created_at + interval '250 milliseconds' <= clock_timestamp(),activation_revision
      FROM finance.hpay_purchases WHERE id=$1::uuid AND buyer_id=$2::uuid AND product_id=$3::bigint FOR UPDATE
    )SQL",
                                    id, buyer, product);
    if (purchase.empty()) throw DomainError(ErrorCode::kNotFound, "HPay purchase not found");
    const auto row = purchase[0];
    if (row[0].as<std::string>() != "pending") return Receipt(tx, id);
    if (!row[5].as<bool>()) throw DomainError(ErrorCode::kConflict, "Повторите команду после сообщения HPay");
    const auto available = sql::Exec(tx,
                                     "SELECT 1 FROM finance.hpay_products WHERE id=$1::bigint "
                                     "AND status='active' AND activation_revision=$2::bigint FOR SHARE",
                                     product, row[6].as<long long>());
    if (available.empty() || !ReceivingAccount(tx, row[1].c_str(), row[2].c_str())) {
      sql::Exec(tx, "UPDATE finance.hpay_purchases SET status='failed' WHERE id=$1::uuid", id);
      return Receipt(tx, id);
    }
    const auto source =
        sql::Exec(tx,
                  "SELECT a.id::text FROM finance.primary_accounts p JOIN finance.accounts a "
                  "ON a.id=p.account_id WHERE p.principal_id=$1::uuid AND a.owner_principal_id=$1::uuid "
                  "AND a.account_type='personal' AND a.status='active'",
                  buyer);
    if (source.empty()) throw DomainError(ErrorCode::kConflict, "Основной личный счёт недоступен");
    TransferCommand cmd{buyer,
                        source[0][0].c_str(),
                        row[2].c_str(),
                        "hpay:" + id,
                        std::string(64, '0'),
                        "HPay H-" + product,
                        id,
                        r.context().request_id(),
                        r.context().correlation_id(),
                        "",
                        Money::Parse(row[3].c_str()),
                        "atm",
                        row[4].c_str()};
    const auto result = PostgresLedger::AtmSettlement(tx, cmd, "hpay");
    sql::Exec(tx,
              "UPDATE finance.hpay_purchases SET status='completed',operation_id=$2::uuid,"
              "completed_at=clock_timestamp(),protected_until=clock_timestamp()+"
              "make_interval(secs=>GREATEST(1,LEAST(60,$3::integer))) WHERE id=$1::uuid",
              id, result.id, business::Field(tx, r.payload(), "protection_seconds", "3"));
    return Receipt(tx, id);
  }

  const auto recent =
      sql::Exec(tx,
                "SELECT id::text FROM finance.hpay_purchases WHERE buyer_id=$1::uuid "
                "AND product_id=$2::bigint AND status='completed' AND protected_until>clock_timestamp() "
                "ORDER BY completed_at DESC LIMIT 1",
                buyer, product);
  if (!recent.empty()) return Receipt(tx, recent[0][0].c_str());
  const auto item = sql::Exec(tx,
                              "SELECT p.organization_id::text,p.account_id::text,p.name,o.name,p.price::text,"
                              "p.activation_revision "
                              "FROM finance.hpay_products p JOIN finance.organizations o ON o.id=p.organization_id "
                              "WHERE p.id=$1::bigint AND p.status='active' FOR SHARE OF p",
                              product);
  if (item.empty() || !ReceivingAccount(tx, item[0][0].c_str(), item[0][1].c_str()))
    throw DomainError(ErrorCode::kNotFound, "Товар HPay недоступен");
  const auto row = item[0];
  sql::Exec(tx,
            "UPDATE finance.hpay_purchases SET status=CASE WHEN product_id=$2::bigint THEN 'failed' ELSE "
            "'cancelled' END WHERE buyer_id=$1::uuid AND status='pending' "
            "AND (product_id<>$2::bigint OR activation_revision<>$3::bigint)",
            buyer, product, row[5].as<long long>());
  const auto existing = sql::Exec(tx,
                                  "SELECT id::text FROM finance.hpay_purchases WHERE buyer_id=$1::uuid "
                                  "AND product_id=$2::bigint AND status='pending'",
                                  buyer, product);
  if (!existing.empty()) return Receipt(tx, existing[0][0].c_str());
  const auto id =
      sql::One(tx,
               "INSERT INTO finance.hpay_purchases(buyer_id,product_id,organization_id,account_id,"
               "product_name,merchant_name,price,activation_revision) "
               "VALUES($1::uuid,$2::bigint,$3::uuid,$4::uuid,$5,$6,$7::numeric,$8::bigint) RETURNING id::text",
               buyer, product, row[0].c_str(), row[1].c_str(), row[2].c_str(), row[3].c_str(), row[4].c_str(),
               row[5].as<long long>())[0]
          .as<std::string>();
  return Receipt(tx, id);
}
}  // namespace horizon::hpay

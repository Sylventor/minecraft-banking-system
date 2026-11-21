#include "horizon/exchange_internal.hpp"

namespace horizon::exchange {
namespace {
void Mint(pqxx::work& tx, const std::string& stock, const std::string& actor, std::int64_t qty,
          const std::string& reason) {
  const auto a =
      sql::One(tx, "SELECT treasury_account_id::text FROM finance.exchange_stocks WHERE id=$1::uuid", stock)[0]
          .as<std::string>();
  const auto id = sql::One(tx,
                           "INSERT INTO finance.exchange_issuances(stock_id,quantity,actor_id,reason) "
                           "VALUES($1::uuid,$2::bigint,$3::uuid,$4) RETURNING id::text",
                           stock, qty, actor, reason)[0]
                      .as<std::string>();
  sql::Exec(tx,
            "INSERT INTO finance.exchange_positions(stock_id,account_id,quantity) VALUES($1::uuid,$2::uuid,$3::bigint) "
            "ON CONFLICT(stock_id,account_id) DO UPDATE SET quantity=exchange_positions.quantity+EXCLUDED.quantity",
            stock, a, qty);
  sql::Exec(tx,
            "INSERT INTO finance.exchange_share_entries(stock_id,account_id,quantity,event_id) "
            "VALUES($1::uuid,$2::uuid,$3::bigint,$4::uuid)",
            stock, a, qty, id);
}
}  // namespace
std::string Corporate(pqxx::work& tx, const Request& r) {
  const auto action = r.action(), actor = r.context().actor_id(), data = r.payload(), id = r.target_id();
  if (action == "issuer.create") {
    const auto org = Field(tx, data, "organization_id"), account = Field(tx, data, "account_id");
    Id(org);
    Id(account);
    Require(tx, actor, org, "exchange.issuer.manage");
    if (sql::Exec(tx,
                  "SELECT id FROM finance.accounts WHERE id=$1::uuid AND organization_id=$2::uuid AND "
                  "account_type='organization' AND status='active'",
                  account, org)
            .empty())
      Fail("owned active organization treasury required");
    const auto stock =
        sql::One(
            tx,
            "INSERT INTO "
            "finance.exchange_stocks(organization_id,treasury_account_id,ticker,name,description,terms,rights,total_"
            "quantity,created_by) VALUES($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8::bigint,$9::uuid) RETURNING id::text",
            org, account, Field(tx, data, "ticker"), Field(tx, data, "name"), Field(tx, data, "description"),
            Field(tx, data, "terms"), Field(tx, data, "rights"), Field(tx, data, "quantity"), actor)[0]
            .as<std::string>();
    Audit(tx, actor, "exchange.issuer.create", stock, data);
    return Stock(tx, stock);
  }
  if (action == "issuer.submit") {
    Issuer(tx, r, id);
    if (sql::Exec(tx,
                  "UPDATE finance.exchange_stocks SET status='pending_review' WHERE id=$1::uuid AND status='draft' "
                  "RETURNING id",
                  id)
            .empty())
      Fail("draft issuer required");
    Audit(tx, actor, "exchange.issuer.submit", id);
    return Stock(tx, id);
  }
  if (action == "issuer.moderate") {
    Authority(tx, actor, "exchange.manage");
    const auto s = sql::Exec(
        tx, "SELECT status,created_by::text,total_quantity FROM finance.exchange_stocks WHERE id=$1::uuid FOR UPDATE",
        id);
    if (s.empty()) throw DomainError(ErrorCode::kNotFound, "issuer not found");
    if (Field(tx, data, "reason").empty()) throw std::invalid_argument("administrative reason required");
    const auto state = Field(tx, data, "status");
    const auto previous = s[0][0].as<std::string>();
    if (state == "approved" || state == "rejected") {
      if (previous != "pending_review") Fail("pending review required");
      if (actor == s[0][1].as<std::string>()) throw DomainError(ErrorCode::kDenied, "cannot moderate own issue");
      if (state == "approved") Mint(tx, id, actor, s[0][2].as<std::int64_t>(), Field(tx, data, "reason"));
    } else if (!((state == "suspended" && previous == "active") || (state == "active" && previous == "suspended") ||
                 (state == "delisted" && (previous == "active" || previous == "suspended"))))
      Fail("invalid listing transition");
    sql::Exec(tx,
              "UPDATE finance.exchange_stocks SET status=$2,approved_by=CASE WHEN $2='approved' THEN $3::uuid ELSE "
              "approved_by END,reason=$4 WHERE id=$1::uuid",
              id, state, actor, Field(tx, data, "reason"));
    if (state == "delisted")
      for (const auto& row : sql::Exec(tx,
                                       "SELECT id::text FROM finance.exchange_orders WHERE stock_id=$1::uuid AND "
                                       "status IN ('open','partially_filled')",
                                       id))
        Cancel(tx, row[0].c_str(), "cancelled");
    Notice(tx, s[0][1].c_str(), "listing_" + state, Stock(tx, id));
    if (state == "suspended")
      for (const auto& user : sql::Exec(tx,
                                        "SELECT DISTINCT actor_id::text FROM finance.exchange_orders WHERE "
                                        "stock_id=$1::uuid AND status IN ('open','partially_filled')",
                                        id))
        Notice(tx, user[0].c_str(), "listing_suspended", Stock(tx, id));
    Audit(tx, actor, "exchange.issuer." + state, id, data);
    return Stock(tx, id);
  }
  if (action == "issuer.additional") {
    Authority(tx, actor, "exchange.manage");
    const auto q = std::stoll(Field(tx, data, "quantity"));
    if (q < 1 || Field(tx, data, "reason").empty())
      throw std::invalid_argument("additional issuance requires quantity and reason");
    if (sql::Exec(tx,
                  "UPDATE finance.exchange_stocks SET total_quantity=total_quantity+$2::bigint WHERE id=$1::uuid AND "
                  "status IN ('approved','active','suspended') AND total_quantity+$2::bigint<=1000000000 RETURNING id",
                  id, q)
            .empty())
      Fail("additional issuance outside approved limits");
    Mint(tx, id, actor, q, Field(tx, data, "reason"));
    Audit(tx, actor, "exchange.issuer.additional", id, data);
    return Stock(tx, id);
  }
  if (action == "ipo.create") {
    Issuer(tx, r, id);
    const std::int64_t qty = std::stoll(Field(tx, data, "quantity"));
    Cost(Money::Parse(Field(tx, data, "price")), qty);
    if (!sql::One(tx, "SELECT $1::timestamptz<=$2::timestamptz AND $2::timestamptz>clock_timestamp()",
                  Field(tx, data, "starts_at"), Field(tx, data, "ends_at"))[0]
             .as<bool>())
      throw std::invalid_argument("invalid IPO dates");
    const auto s = sql::Exec(tx,
                             "UPDATE finance.exchange_stocks SET status='active' WHERE id=$1::uuid AND "
                             "status='approved' RETURNING treasury_account_id::text",
                             id);
    if (s.empty()) Fail("approved unlisted issue required");
    if (sql::Exec(tx,
                  "UPDATE finance.exchange_positions SET reserved=reserved+$3::bigint WHERE stock_id=$1::uuid AND "
                  "account_id=$2::uuid AND quantity-reserved>=$3::bigint RETURNING stock_id",
                  id, s[0][0].c_str(), qty)
            .empty())
      Fail("not enough treasury shares");
    sql::Exec(tx,
              "INSERT INTO finance.exchange_ipos(stock_id,quantity,remaining,price,starts_at,ends_at,max_per_buyer) "
              "VALUES($1::uuid,$2::bigint,$2::bigint,$3::numeric,$4::timestamptz,$5::timestamptz,$6::bigint)",
              id, qty, Field(tx, data, "price"), Field(tx, data, "starts_at"), Field(tx, data, "ends_at"),
              Field(tx, data, "max_per_buyer", "1000000000"));
    Audit(tx, actor, "exchange.ipo.create", id, data);
    return Stock(tx, id);
  }
  if (action == "ipo.buy") {
    const auto account = Field(tx, data, "account_id");
    Account(tx, r, account);
    const std::int64_t qty = std::stoll(Field(tx, data, "quantity"));
    const auto s = sql::Exec(tx,
                             "SELECT i.id::text,s.treasury_account_id::text,i.price::text,i.remaining,i.max_per_buyer "
                             "FROM finance.exchange_ipos i JOIN finance.exchange_stocks s ON s.id=i.stock_id WHERE "
                             "s.id=$1::uuid AND s.status='active' AND i.status='open' AND "
                             "clock_timestamp()>=i.starts_at AND clock_timestamp()<i.ends_at FOR UPDATE OF i",
                             id);
    if (s.empty()) Fail("IPO not available");
    const auto i = s[0];
    if (i[1].as<std::string>() == account ||
        sql::One(tx,
                 "SELECT EXISTS(SELECT 1 FROM finance.exchange_stocks s JOIN finance.organizations o ON "
                 "o.id=s.organization_id JOIN finance.accounts a ON a.id=$3::uuid WHERE s.id=$1::uuid AND "
                 "(o.owner_id=$2::uuid OR a.organization_id=s.organization_id))",
                 id, actor, account)[0]
            .as<bool>())
      Fail("issuer cannot buy its own IPO");
    if (qty < 1 || qty > i[3].as<std::int64_t>() ||
        sql::One(tx,
                 "SELECT COALESCE(sum(t.quantity),0)+$3::bigint FROM finance.exchange_trades t JOIN finance.accounts a "
                 "ON a.id=t.buyer_account_id WHERE ipo_id=$1::uuid AND "
                 "COALESCE(a.owner_principal_id,a.organization_id)=(SELECT "
                 "COALESCE(owner_principal_id,organization_id) FROM finance.accounts WHERE id=$2::uuid)",
                 i[0].c_str(), account, qty)[0]
                .as<std::int64_t>() > i[4].as<std::int64_t>())
      Fail("IPO quantity or buyer cap exceeded");
    const auto price = Money::Parse(i[2].c_str()), cost = Cost(price, qty);
    const auto tid = NewId(tx);
    const auto op = Pay(tx, actor, account, i[1].c_str(), cost, tid, "Первичное размещение акций");
    sql::Exec(tx, "UPDATE finance.exchange_ipos SET remaining=remaining-$2::bigint WHERE id=$1::uuid", i[0].c_str(),
              qty);
    sql::Exec(tx,
              "UPDATE finance.exchange_positions SET reserved=reserved-$3::bigint WHERE stock_id=$1::uuid AND "
              "account_id=$2::uuid",
              id, i[1].c_str(), qty);
    Shares(tx, id, i[1].c_str(), account, qty, cost, tid);
    sql::Exec(
        tx,
        "INSERT INTO "
        "finance.exchange_trades(id,stock_id,ipo_id,buyer_account_id,seller_account_id,quantity,price,total,operation_"
        "id) VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$6::bigint,$7::numeric,$8::numeric,$9::uuid)",
        tid, id, i[0].c_str(), account, i[1].c_str(), qty, price.ToString(), cost.ToString(), op.id);
    const auto out = sql::One(tx,
                              "SELECT "
                              "jsonb_build_object('trade_id',$1::text,'operation_id',$2::text,'quantity',$3::bigint,'"
                              "price',$4::text,'amount',$5::text,'ticker',(SELECT ticker FROM finance.exchange_stocks "
                              "WHERE id=$6::uuid))::text",
                              tid, op.id, qty, price.ToString(), cost.ToString(), id)[0]
                         .as<std::string>();
    Notice(tx, actor, "ipo_buy", out);
    for (const auto& u :
         sql::Exec(tx,
                   "SELECT m.principal_id::text FROM finance.organization_members m JOIN finance.exchange_stocks s ON "
                   "s.organization_id=m.organization_id WHERE s.id=$1::uuid AND m.revoked_at IS NULL AND "
                   "'exchange.issuer.manage'=ANY(m.permissions)",
                   id))
      Notice(tx, u[0].c_str(), "sell", out);
    Audit(tx, actor, "exchange.ipo.buy", tid, data);
    return out;
  }
  if (action == "dividend.create") {
    const auto stock = Field(tx, data, "stock_id"), account = Field(tx, data, "account_id");
    Issuer(tx, r, stock, "exchange.dividends.manage");
    Account(tx, r, account, "exchange.dividends.manage");
    if (sql::Exec(tx,
                  "SELECT id FROM finance.exchange_stocks WHERE id=$1::uuid AND treasury_account_id=$2::uuid AND "
                  "status IN ('active','suspended','delisted')",
                  stock, account)
            .empty())
      Fail("issuer treasury required");
    Money::Parse(Field(tx, data, "amount"));
    const auto d =
        sql::One(tx,
                 "INSERT INTO "
                 "finance.exchange_dividends(stock_id,source_account_id,created_by,record_at,pay_at,amount,mode) "
                 "VALUES($1::uuid,$2::uuid,$3::uuid,$4::timestamptz,$5::timestamptz,$6::numeric,$7) RETURNING id::text",
                 stock, account, actor, Field(tx, data, "record_at"), Field(tx, data, "pay_at"),
                 Field(tx, data, "amount"), Field(tx, data, "mode"))[0]
            .as<std::string>();
    Audit(tx, actor, "exchange.dividend.create", d, data);
    return Dividend(tx, d);
  }
  if (action == "dividend.approve" || action == "dividend.execute") {
    const auto d = sql::Exec(
        tx,
        "SELECT "
        "stock_id::text,source_account_id::text,created_by::text,COALESCE(approved_by::text,''),status,record_at::text,"
        "pay_at::text,amount::text,mode,(SELECT organization_id::text FROM finance.exchange_stocks s WHERE "
        "s.id=d.stock_id) FROM finance.exchange_dividends d WHERE id=$1::uuid FOR UPDATE",
        id);
    if (d.empty()) throw DomainError(ErrorCode::kNotFound, "dividend not found");
    const auto row = d[0];
    Issuer(tx, r, row[0].c_str(), "exchange.dividends.manage");
    Account(tx, r, row[1].c_str(), "exchange.dividends.manage");
    if (action == "dividend.approve") {
      if (actor == row[2].as<std::string>() && !IsOwner(tx, actor, row[9].c_str()))
        throw DomainError(ErrorCode::kDenied, "another authorized approver required");
      if (row[4].as<std::string>() != "draft" ||
          !sql::One(tx, "SELECT $1::timestamptz<=clock_timestamp()", row[5].c_str())[0].as<bool>())
        Fail("record date must have passed before snapshot approval");
      const auto holders =
          sql::Exec(tx,
                    "SELECT account_id::text,sum(quantity)::bigint FROM finance.exchange_share_entries WHERE "
                    "stock_id=$1::uuid AND created_at<=$2::timestamptz AND account_id<>$3::uuid GROUP BY account_id "
                    "HAVING sum(quantity)>0 ORDER BY account_id",
                    row[0].c_str(), row[5].c_str(), row[1].c_str());
      std::int64_t total_qty = 0;
      for (const auto& h : holders) total_qty += h[1].as<std::int64_t>();
      if (total_qty == 0 || holders.size() > 1000) Fail("no eligible holders or distribution exceeds 1000 recipients");
      Money total;
      const auto amount = Money::Parse(row[7].c_str());
      for (const auto& h : holders) {
        const auto payout =
            row[8].as<std::string>() == "per_share"
                ? Cost(amount, h[1].as<std::int64_t>())
                : Money::Parse(
                      sql::One(tx, "SELECT (floor($1::numeric*100*$2::bigint/$3::bigint)/100)::numeric(18,2)::text",
                               amount.ToString(), h[1].as<std::int64_t>(), total_qty)[0]
                          .c_str());
        if (payout.cents() == 0) continue;
        total = total + payout;
        sql::Exec(tx,
                  "INSERT INTO finance.exchange_distributions(dividend_id,account_id,quantity,amount) "
                  "VALUES($1::uuid,$2::uuid,$3::bigint,$4::numeric)",
                  id, h[0].c_str(), h[1].as<std::int64_t>(), payout.ToString());
      }
      if (total.cents() == 0) Fail("dividend rounds to zero");
      sql::Exec(
          tx,
          "UPDATE finance.exchange_dividends SET "
          "status='approved',approved_by=$2::uuid,total=$3::numeric,rounding_remainder=$4::numeric WHERE id=$1::uuid",
          id, actor, total.ToString(), row[8].as<std::string>() == "total" ? (amount - total).ToString() : "0.00");
    } else {
      if (row[4].as<std::string>() == "completed") return Dividend(tx, id);
      if (row[4].as<std::string>() != "approved") Fail("approved unpaid dividend required");
      Issuer(tx, r, row[0].c_str(), "exchange.dividends.manage");
      const auto org =
          sql::One(tx, "SELECT organization_id::text FROM finance.exchange_stocks WHERE id=$1::uuid", row[0].c_str())[0]
              .as<std::string>();
      Require(tx, row[3].c_str(), org, "exchange.dividends.manage");
      if (!sql::One(tx, "SELECT $1::timestamptz<=clock_timestamp()", row[6].c_str())[0].as<bool>())
        Fail("dividend payment date has not arrived");
      const auto enough = sql::One(tx,
                                   "SELECT balance-held>=(SELECT total FROM finance.exchange_dividends WHERE "
                                   "id=$2::uuid) FROM finance.accounts WHERE id=$1::uuid FOR UPDATE",
                                   row[1].c_str(), id)[0]
                              .as<bool>();
      if (!enough) throw DomainError(ErrorCode::kInsufficient, "insufficient funds for entire dividend");
      // Every distribution uses the existing core transfer inside this shared
      // transaction. A failure rolls back the whole dividend, including prior rows.
      for (const auto& i : sql::Exec(tx,
                                     "SELECT id::text,account_id::text,amount::text FROM "
                                     "finance.exchange_distributions WHERE dividend_id=$1::uuid ORDER BY account_id",
                                     id)) {
        const auto op =
            Pay(tx, actor, row[1].c_str(), i[1].c_str(), Money::Parse(i[2].c_str()), i[0].c_str(), "Дивиденды");
        sql::Exec(
            tx, "UPDATE finance.exchange_distributions SET status='completed',operation_id=$2::uuid WHERE id=$1::uuid",
            i[0].c_str(), op.id);
        sql::Exec(tx,
                  "UPDATE finance.exchange_positions SET dividends_cents=dividends_cents+$3::numeric*100 WHERE "
                  "stock_id=$1::uuid AND account_id=$2::uuid",
                  row[0].c_str(), i[1].c_str(), i[2].c_str());
        for (const auto& u : sql::Exec(tx,
                                       "SELECT owner_principal_id::text FROM finance.accounts WHERE id=$1::uuid AND "
                                       "owner_principal_id IS NOT NULL",
                                       i[1].c_str()))
          Notice(tx, u[0].c_str(), "dividend",
                 sql::One(tx, "SELECT jsonb_build_object('amount',$1::text,'operation_id',$2::text)::text",
                          i[2].c_str(), op.id)[0]
                     .as<std::string>());
      }
      sql::Exec(tx, "UPDATE finance.exchange_dividends SET status='completed',executor_id=$2::uuid WHERE id=$1::uuid",
                id, actor);
    }
    Audit(tx, actor, "exchange." + action, id, data);
    return Dividend(tx, id);
  }
  throw std::invalid_argument("unknown exchange command");
}
}  // namespace horizon::exchange

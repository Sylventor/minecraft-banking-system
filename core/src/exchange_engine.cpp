#include "horizon/exchange_internal.hpp"

namespace horizon::exchange {
void Reserve(pqxx::work& tx, const std::string& id, Money amount) {
  const auto account =
      sql::One(tx, "SELECT account_id::text FROM finance.exchange_orders WHERE id=$1::uuid", id)[0].as<std::string>();
  const auto balance =
      sql::One(tx, "SELECT balance::text,held::text FROM finance.accounts WHERE id=$1::uuid FOR UPDATE", account);
  const auto old = Money::Parse(sql::One(tx,
                                         "SELECT COALESCE((SELECT amount FROM finance.exchange_reserves WHERE "
                                         "order_id=$1::uuid),0)::numeric(18,2)::text",
                                         id)[0]
                                    .c_str());
  if (Money::Parse(balance[0].c_str()) - Money::Parse(balance[1].c_str()) + old < amount)
    throw DomainError(ErrorCode::kInsufficient, "insufficient available funds for order reserve");
  sql::Exec(tx, "UPDATE finance.accounts SET held=held-$2::numeric+$3::numeric WHERE id=$1::uuid", account,
            old.ToString(), amount.ToString());
  sql::Exec(tx,
            "INSERT INTO finance.exchange_reserves(order_id,account_id,amount) VALUES($1::uuid,$2::uuid,$3::numeric) "
            "ON CONFLICT(order_id) DO UPDATE SET amount=EXCLUDED.amount",
            id, account, amount.ToString());
}
void Cancel(pqxx::work& tx, const std::string& id, const std::string& state) {
  const auto o = sql::One(tx,
                          "SELECT side,stock_id::text,account_id::text,remaining,actor_id::text,status FROM "
                          "finance.exchange_orders WHERE id=$1::uuid FOR UPDATE",
                          id);
  if (o[5].as<std::string>() != "open" && o[5].as<std::string>() != "partially_filled") return;
  if (o[0].as<std::string>() == "BUY")
    Reserve(tx, id, Money());
  else
    sql::Exec(tx,
              "UPDATE finance.exchange_positions SET reserved=reserved-$3::bigint WHERE stock_id=$1::uuid AND "
              "account_id=$2::uuid",
              o[1].c_str(), o[2].c_str(), o[3].as<std::int64_t>());
  sql::Exec(tx, "UPDATE finance.exchange_orders SET status=$2 WHERE id=$1::uuid", id, state);
  Notice(tx, o[4].c_str(), "order_" + state, Order(tx, id));
  Audit(tx, o[4].c_str(), "exchange.order." + state, id);
}
void Expire(pqxx::work& tx) {
  for (const auto& row :
       sql::Exec(tx,
                 "SELECT id::text FROM finance.exchange_orders WHERE status IN ('open','partially_filled') AND "
                 "expires_at<=clock_timestamp() ORDER BY sequence LIMIT 100"))
    Cancel(tx, row[0].c_str(), "expired");
  const auto ipos = sql::Exec(tx,
                              "UPDATE finance.exchange_ipos SET status='closed' WHERE status='open' AND "
                              "ends_at<=clock_timestamp() RETURNING stock_id::text,remaining");
  for (const auto& i : ipos)
    sql::Exec(tx,
              "UPDATE finance.exchange_positions SET reserved=reserved-$2::bigint WHERE stock_id=$1::uuid AND "
              "account_id=(SELECT treasury_account_id FROM finance.exchange_stocks WHERE id=$1::uuid)",
              i[0].c_str(), i[1].as<std::int64_t>());
}
void Shares(pqxx::work& tx, const std::string& stock, const std::string& source, const std::string& dest,
            std::int64_t qty, Money cost, const std::string& event) {
  sql::Exec(
      tx,
      "INSERT INTO finance.exchange_positions(stock_id,account_id) VALUES($1::uuid,$2::uuid) ON CONFLICT DO NOTHING",
      stock, dest);
  sql::Exec(tx,
            "UPDATE finance.exchange_positions SET "
            "realized_cents=realized_cents+$4::numeric-floor(cost_cents*$3::bigint/"
            "quantity),cost_cents=cost_cents-floor(cost_cents*$3::bigint/quantity),quantity=quantity-$3::bigint WHERE "
            "stock_id=$1::uuid AND account_id=$2::uuid AND quantity>=$3::bigint",
            stock, source, qty, cost.cents());
  sql::Exec(tx,
            "UPDATE finance.exchange_positions SET quantity=quantity+$3::bigint,cost_cents=cost_cents+$4::numeric "
            "WHERE stock_id=$1::uuid AND account_id=$2::uuid",
            stock, dest, qty, cost.cents());
  sql::Exec(tx,
            "INSERT INTO finance.exchange_share_entries(stock_id,account_id,quantity,event_id) "
            "VALUES($1::uuid,$2::uuid,-$4::bigint,$5::uuid),($1::uuid,$3::uuid,$4::bigint,$5::uuid)",
            stock, source, dest, qty, event);
}
std::string SubmitOrder(pqxx::work& tx, const Request& r) {
  const auto data = r.payload(), actor = r.context().actor_id(), account = Field(tx, data, "account_id"),
             ticker = Field(tx, data, "ticker"), side = Field(tx, data, "side"), kind = Field(tx, data, "kind");
  Account(tx, r, account);
  if (side != "BUY" && side != "SELL") throw std::invalid_argument("invalid side");
  if (kind != "limit" && kind != "market") throw std::invalid_argument("invalid order kind");
  const auto stock =
      sql::Exec(tx,
                "SELECT id::text FROM finance.exchange_stocks WHERE ticker=$1 AND status='active' AND EXISTS(SELECT 1 "
                "FROM finance.exchange_ipos i WHERE i.stock_id=exchange_stocks.id AND i.ends_at<=clock_timestamp())",
                ticker);
  if (stock.empty()) Fail("secondary trading is not active; IPO must have ended");
  const auto sid = stock[0][0].as<std::string>();
  const std::int64_t qty = std::stoll(Field(tx, data, "quantity"));
  const auto price = Money::Parse(Field(tx, data, "price"));
  const auto maximum = Cost(price, qty);
  auto budget = maximum;
  if (kind == "market" && side == "BUY") budget = Money::Parse(Field(tx, data, "budget"));
  if (budget.cents() == 0) throw std::invalid_argument("positive cash budget required");
  const auto expires = Field(tx, data, "expires_at",
                             sql::One(tx, "SELECT (clock_timestamp()+interval '30 days')::text")[0].as<std::string>());
  if (!sql::One(tx,
                "SELECT $1::timestamptz>clock_timestamp() AND $1::timestamptz<=clock_timestamp()+interval '90 days'",
                expires)[0]
           .as<bool>())
    throw std::invalid_argument("invalid order expiry");
  const auto id =
      sql::One(tx,
               "INSERT INTO "
               "finance.exchange_orders(stock_id,actor_id,account_id,side,kind,quantity,remaining,price,expires_at) "
               "VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5,$6::bigint,$6::bigint,$7::numeric,$8::timestamptz) RETURNING "
               "id::text",
               sid, actor, account, side, kind, qty, price.ToString(), expires)[0]
          .as<std::string>();
  if (side == "BUY")
    Reserve(tx, id, budget);
  else {
    const auto p =
        sql::Exec(tx,
                  "UPDATE finance.exchange_positions SET reserved=reserved+$3::bigint WHERE stock_id=$1::uuid AND "
                  "account_id=$2::uuid AND quantity-reserved>=$3::bigint RETURNING quantity",
                  sid, account, qty);
    if (p.empty()) throw DomainError(ErrorCode::kInsufficient, "insufficient available shares");
  }
  auto remaining = qty;
  // A global transactional exchange lock gives every order an unambiguous sequence.
  // Resting order price, then sequence, determine deterministic maker priority.
  int matches = 0;
  while (remaining > 0) {
    const auto makers = sql::Exec(
        tx,
        "SELECT id::text,account_id::text,actor_id::text,remaining,price::text FROM finance.exchange_orders WHERE "
        "stock_id=$1::uuid AND side<>$2 AND status IN ('open','partially_filled') AND expires_at>clock_timestamp() AND "
        "(($2='BUY' AND price<=$3::numeric) OR ($2='SELL' AND price>=$3::numeric)) ORDER BY CASE WHEN $2='BUY' THEN "
        "price END ASC,CASE WHEN $2='SELL' THEN price END DESC,sequence LIMIT 1 FOR UPDATE",
        sid, side, price.ToString());
    if (makers.empty()) break;
    if (++matches > 200) throw DomainError(ErrorCode::kLimit, "order exceeds 200 matching fills; reduce quantity");
    const auto m = makers[0];
    const auto maker = m[0].as<std::string>(), ma = m[1].as<std::string>(), mu = m[2].as<std::string>();
    if (actor == mu || account == ma ||
        sql::One(tx,
                 "SELECT EXISTS(SELECT 1 FROM finance.accounts a JOIN finance.accounts b ON "
                 "a.owner_principal_id=b.owner_principal_id OR a.organization_id=b.organization_id WHERE a.id=$1::uuid "
                 "AND b.id=$2::uuid)",
                 account, ma)[0]
            .as<bool>())
      Fail("self trading is prohibited");
    bool eligible = true;
    try {
      Request mandate;
      mandate.mutable_context()->set_actor_id(mu);
      Account(tx, mandate, ma);
    } catch (const DomainError&) {
      eligible = false;
    }
    if (!eligible) {
      Cancel(tx, maker, "cancelled");
      continue;
    }
    const auto deal_price = Money::Parse(m[4].c_str());
    auto filled = std::min(remaining, m[3].as<std::int64_t>());
    if (side == "BUY" && kind == "market") filled = std::min(filled, budget.cents() / deal_price.cents());
    if (filled == 0) break;
    const auto total = Cost(deal_price, filled);
    const auto buy = side == "BUY" ? id : maker, sell = side == "SELL" ? id : maker,
               buyer = side == "BUY" ? account : ma, seller = side == "SELL" ? account : ma,
               buyer_actor = side == "BUY" ? actor : mu;
    // Release this order's reserve inside the very same transaction. Move cannot
    // observe or spend another order's funds; no intermediate state can commit.
    Reserve(tx, buy, Money());
    sql::Exec(tx,
              "UPDATE finance.exchange_positions SET reserved=reserved-$3::bigint WHERE stock_id=$1::uuid AND "
              "account_id=$2::uuid",
              sid, seller, filled);
    const auto tid = NewId(tx);
    const auto op = Pay(tx, buyer_actor, buyer, seller, total, tid, "Биржевая сделка " + ticker);
    Shares(tx, sid, seller, buyer, filled, total, tid);
    sql::Exec(
        tx,
        "INSERT INTO "
        "finance.exchange_trades(id,stock_id,buy_order_id,sell_order_id,buyer_account_id,seller_account_id,quantity,"
        "price,total,operation_id) "
        "VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$6::uuid,$7::bigint,$8::numeric,$9::numeric,$10::uuid)",
        tid, sid, buy, sell, buyer, seller, filled, deal_price.ToString(), total.ToString(), op.id);
    sql::Exec(tx,
              "UPDATE finance.exchange_orders SET remaining=remaining-$2::bigint,status=CASE WHEN remaining=$2::bigint "
              "THEN 'filled' ELSE 'partially_filled' END WHERE id=$1::uuid OR id=$3::uuid",
              id, filled, maker);
    remaining -= filled;
    if (side == "BUY" && kind == "market") budget = budget - total;
    const auto br = sql::One(tx, "SELECT remaining,price::text FROM finance.exchange_orders WHERE id=$1::uuid", buy);
    Reserve(tx, buy,
            br[0].as<std::int64_t>() == 0       ? Money()
            : side == "BUY" && kind == "market" ? budget
                                                : Cost(Money::Parse(br[1].c_str()), br[0].as<std::int64_t>()));
    const auto payload = sql::One(tx,
                                  "SELECT "
                                  "jsonb_build_object('ticker',$1::text,'quantity',$2::bigint,'price',$3::text,'amount'"
                                  ",$4::text,'operation_id',$5::text)::text",
                                  ticker, filled, deal_price.ToString(), total.ToString(), op.id)[0]
                             .as<std::string>();
    Notice(tx, buyer_actor, "buy", payload);
    Notice(tx, side == "SELL" ? actor : mu, "sell", payload);
    Notice(tx, mu, m[3].as<std::int64_t>() == filled ? "order_filled" : "order_partial", Order(tx, maker));
  }
  if (kind == "market" && remaining > 0)
    Cancel(tx, id, "cancelled");
  else
    Notice(tx, actor,
           remaining == 0     ? "order_filled"
           : remaining == qty ? "order_open"
                              : "order_partial",
           Order(tx, id));
  Audit(tx, actor, "exchange.order.create", id, data);
  return Order(tx, id);
}
}  // namespace horizon::exchange

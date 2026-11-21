#include "horizon/exchange_internal.hpp"

namespace horizon::exchange {
std::string Read(pqxx::work& tx, const Request& r) {
  const auto actor = r.context().actor_id(), action = r.action(), data = r.payload().empty() ? "{}" : r.payload();
  const auto limit = std::clamp(std::stoi(Field(tx, data, "limit", "25")), 1, 100);
  const auto cursor = Field(tx, data, "cursor"), ticker = Field(tx, data, "ticker"),
             account = Field(tx, data, "account_id");
  if (action == "read.ticker_availability")
    return sql::One(tx,
                    "SELECT jsonb_build_object('available',NOT EXISTS(SELECT 1 FROM finance.exchange_stocks "
                    "WHERE ticker=$1))::text",
                    ticker)[0]
        .as<std::string>();
  if (action == "read.stocks")
    return Page(
        sql::Exec(tx, R"SQL(SELECT s.id::text,(jsonb_build_object('id',s.id,'ticker',ticker,'name',name,'status',status,
    'description',description,'total_quantity',total_quantity,'last_price',(SELECT price::text FROM finance.exchange_trades WHERE stock_id=s.id ORDER BY sequence DESC LIMIT 1),
    'last_trade_at',(SELECT created_at FROM finance.exchange_trades WHERE stock_id=s.id ORDER BY sequence DESC LIMIT 1),
    'volume24',(SELECT COALESCE(sum(quantity),0) FROM finance.exchange_trades WHERE stock_id=s.id AND created_at>clock_timestamp()-interval '24 hours')))::text
    FROM finance.exchange_stocks s WHERE status IN ('approved','active','suspended','delisted') AND ($1='' OR position(lower($1) in lower(ticker||' '||name))>0)
    AND ($2='' OR id>NULLIF($2,'')::uuid) ORDER BY id LIMIT $3)SQL",
                  Field(tx, data, "q"), cursor, limit + 1),
        limit);
  if (action == "read.stock" || action == "read.orderbook" || action == "read.trades" || action == "read.candles") {
    const auto found = sql::Exec(tx,
                                 "SELECT id::text FROM finance.exchange_stocks WHERE ticker=$1 AND status IN "
                                 "('approved','active','suspended','delisted')",
                                 ticker);
    if (found.empty()) throw DomainError(ErrorCode::kNotFound, "stock not found");
    const auto id = found[0][0].as<std::string>();
    if (action == "read.stock")
      return sql::
          One(tx,
              R"SQL(SELECT ((to_jsonb(s)-ARRAY['created_by','approved_by','reason','treasury_account_id'])||jsonb_build_object(
      'last_price',(SELECT price::text FROM finance.exchange_trades WHERE stock_id=s.id ORDER BY sequence DESC LIMIT 1),
      'last_trade_at',(SELECT created_at FROM finance.exchange_trades WHERE stock_id=s.id ORDER BY sequence DESC LIMIT 1),
      'change',((SELECT price FROM finance.exchange_trades WHERE stock_id=s.id ORDER BY sequence DESC LIMIT 1)-
        (SELECT price FROM finance.exchange_trades WHERE stock_id=s.id AND created_at<clock_timestamp()-interval '24 hours' ORDER BY sequence DESC LIMIT 1))::numeric(38,2)::text,
      'change_percent',round(100*((SELECT price FROM finance.exchange_trades WHERE stock_id=s.id ORDER BY sequence DESC LIMIT 1)/
        NULLIF((SELECT price FROM finance.exchange_trades WHERE stock_id=s.id AND created_at<clock_timestamp()-interval '24 hours' ORDER BY sequence DESC LIMIT 1),0)-1),2)::text,
      'volume24',(SELECT COALESCE(sum(quantity),0) FROM finance.exchange_trades WHERE stock_id=s.id AND created_at>clock_timestamp()-interval '24 hours'),
      'trades24',(SELECT count(*) FROM finance.exchange_trades WHERE stock_id=s.id AND created_at>clock_timestamp()-interval '24 hours'),
      'best_bid',(SELECT max(price)::text FROM finance.exchange_orders WHERE stock_id=s.id AND side='BUY' AND status IN ('open','partially_filled') AND expires_at>clock_timestamp()),
      'best_ask',(SELECT min(price)::text FROM finance.exchange_orders WHERE stock_id=s.id AND side='SELL' AND status IN ('open','partially_filled') AND expires_at>clock_timestamp()),
      'ipo',(SELECT to_jsonb(i)||jsonb_build_object('price',price::text) FROM finance.exchange_ipos i WHERE stock_id=s.id)))::text
      FROM finance.exchange_stocks s WHERE id=$1::uuid)SQL",
              id)[0]
              .as<std::string>();
    if (action == "read.orderbook")
      return sql::One(tx, R"SQL(SELECT jsonb_build_object('bids',COALESCE((SELECT jsonb_agg(to_jsonb(x)) FROM
      (SELECT price::text price,sum(remaining) quantity,count(*) orders FROM finance.exchange_orders WHERE stock_id=$1::uuid AND side='BUY' AND status IN ('open','partially_filled') AND expires_at>clock_timestamp() GROUP BY price ORDER BY price DESC LIMIT $2) x),'[]'::jsonb),
      'asks',COALESCE((SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT price::text price,sum(remaining) quantity,count(*) orders FROM finance.exchange_orders WHERE stock_id=$1::uuid AND side='SELL' AND status IN ('open','partially_filled') AND expires_at>clock_timestamp() GROUP BY price ORDER BY price LIMIT $2) x),'[]'::jsonb))::text)SQL",
                      id, limit)[0]
          .as<std::string>();
    if (action == "read.trades")
      return Page(sql::Exec(tx,
                            "SELECT "
                            "sequence::text,jsonb_build_object('id',id,'sequence',sequence,'quantity',quantity,'price',"
                            "price::text,'total',total::text,'fee',fee::text,'created_at',created_at,'ipo',ipo_id IS "
                            "NOT NULL)::text FROM finance.exchange_trades WHERE stock_id=$1::uuid AND ($2='' OR "
                            "sequence<NULLIF($2,'')::bigint) ORDER BY sequence DESC LIMIT $3",
                            id, cursor, limit + 1),
                  limit);
    const auto period = Field(tx, data, "period", "24h");
    const std::map<std::string, std::string> windows{{"1h", "1 hour"},   {"24h", "24 hours"}, {"7d", "7 days"},
                                                     {"30d", "30 days"}, {"90d", "90 days"},  {"all", "1000 years"}};
    if (!windows.contains(period)) throw std::invalid_argument("invalid chart period");
    const auto step = period == "1h" || period == "24h" ? "hour" : "day";
    return sql::
        One(tx,
            R"SQL(SELECT jsonb_build_object('items',COALESCE(jsonb_agg(to_jsonb(x) ORDER BY x.at),'[]'::jsonb))::text FROM
      (SELECT date_trunc($3,created_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' at,
      (array_agg(price::text ORDER BY sequence))[1] open,max(price)::text high,min(price)::text low,
      (array_agg(price::text ORDER BY sequence DESC))[1] close,sum(quantity) volume,count(*) trades
      FROM finance.exchange_trades WHERE stock_id=$1::uuid AND created_at>=clock_timestamp()-$2::interval
      GROUP BY 1 ORDER BY 1 DESC LIMIT 1000) x)SQL",
            id, windows.at(period), step)[0]
            .as<std::string>();
  }
  if (action == "read.issuer") {
    const auto stock = r.target_id();
    Issuer(tx, r, stock);
    return Stock(tx, stock);
  }
  if (action == "read.issuers") {
    if (!sql::One(tx,
                  "SELECT EXISTS(SELECT 1 FROM finance.organization_members WHERE organization_id=$1::uuid AND "
                  "principal_id=$2::uuid AND revoked_at IS NULL AND "
                  "permissions&&ARRAY['exchange.issuer.manage','exchange.dividends.manage'])",
                  r.organization_id(), actor)[0]
             .as<bool>())
      throw DomainError(ErrorCode::kDenied, "issuer or dividend permission required");
    return Page(sql::Exec(tx,
                          "SELECT id::text,to_jsonb(s)::text FROM finance.exchange_stocks s WHERE "
                          "organization_id=$1::uuid AND ($2='' OR id>NULLIF($2,'')::uuid) ORDER BY id LIMIT $3",
                          r.organization_id(), cursor, limit + 1),
                limit);
  }
  if (action == "read.admin" || action == "read.audit" || action == "read.admin_orders" ||
      action == "read.admin_trades" || action == "read.issuances" || action == "read.surveillance") {
    Authority(tx, actor, "admin.read");
    if (action == "read.surveillance")
      return sql::One(tx,
                      R"SQL(SELECT jsonb_build_object('items',COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb))::text FROM
      (SELECT actor_id,stock_id,count(*) orders,count(*) FILTER(WHERE status IN ('cancelled','expired')) cancellations,
      max(created_at) latest_order FROM finance.exchange_orders WHERE created_at>=clock_timestamp()-interval '24 hours'
      GROUP BY actor_id,stock_id HAVING count(*)>=10 OR count(*) FILTER(WHERE status IN ('cancelled','expired'))>=5
      ORDER BY count(*) DESC LIMIT $1) x)SQL",
                      limit)[0]
          .as<std::string>();
    if (action == "read.admin")
      return Page(sql::Exec(tx,
                            "SELECT id::text,to_jsonb(s)::text FROM finance.exchange_stocks s WHERE ($1='' OR "
                            "id>NULLIF($1,'')::uuid) AND ($2='' OR status=$2) ORDER BY id LIMIT $3",
                            cursor, Field(tx, data, "status"), limit + 1),
                  limit);
    if (action == "read.admin_orders")
      return Page(sql::Exec(tx,
                            "SELECT sequence::text,(to_jsonb(o)||jsonb_build_object('price',price::text))::text FROM "
                            "finance.exchange_orders o WHERE ($1='' OR sequence<NULLIF($1,'')::bigint) ORDER BY "
                            "sequence DESC LIMIT $2",
                            cursor, limit + 1),
                  limit);
    if (action == "read.admin_trades")
      return Page(sql::Exec(tx,
                            "SELECT "
                            "sequence::text,(to_jsonb(t)||jsonb_build_object('price',price::text,'total',total::text,'"
                            "fee',fee::text))::text FROM finance.exchange_trades t WHERE ($1='' OR "
                            "sequence<NULLIF($1,'')::bigint) ORDER BY sequence DESC LIMIT $2",
                            cursor, limit + 1),
                  limit);
    if (action == "read.issuances")
      return Page(sql::Exec(tx,
                            "SELECT id::text,to_jsonb(i)::text FROM finance.exchange_issuances i WHERE ($1='' OR "
                            "id>NULLIF($1,'')::uuid) ORDER BY id LIMIT $2",
                            cursor, limit + 1),
                  limit);
    return Page(sql::Exec(tx,
                          "SELECT id::text,to_jsonb(e)::text FROM finance.profile_events e WHERE action LIKE "
                          "'business.exchange.%' AND ($1='' OR id>NULLIF($1,'')::uuid) ORDER BY id LIMIT $2",
                          cursor, limit + 1),
                limit);
  }
  if (action == "read.orders" || action == "read.order") {
    const auto rows = sql::Exec(
        tx,
        "SELECT id::text,account_id::text FROM finance.exchange_orders WHERE actor_id=$1::uuid AND ($2='' OR "
        "id=NULLIF($2,'')::uuid) AND ($3='' OR id>NULLIF($3,'')::uuid) AND ($4='' OR status=$4) ORDER BY id LIMIT $5",
        actor, r.target_id(), cursor, Field(tx, data, "status"), limit + 1);
    if (action == "read.order") {
      if (rows.empty()) throw DomainError(ErrorCode::kNotFound, "order not found");
      if (r.resource_scoped() && std::find(r.allowed_account_ids().begin(), r.allowed_account_ids().end(),
                                           rows[0][1].as<std::string>()) == r.allowed_account_ids().end())
        throw DomainError(ErrorCode::kDenied, "account outside API key");
      return Order(tx, rows[0][0].c_str());
    }
    std::string ids;
    for (const auto& a : r.allowed_account_ids()) {
      Id(a);
      if (!ids.empty()) ids += ",";
      ids += a;
    }
    return Page(
        sql::Exec(
            tx,
            "SELECT id::text,(to_jsonb(o)||jsonb_build_object('price',price::text,'fee','0.00'))::text FROM "
            "finance.exchange_orders o WHERE actor_id=$1::uuid AND ($2='' OR id>NULLIF($2,'')::uuid) AND ($3='' OR "
            "status=$3) AND (NOT $4::boolean OR account_id=ANY(string_to_array($5,',')::uuid[])) ORDER BY id LIMIT $6",
            actor, cursor, Field(tx, data, "status"), r.resource_scoped(), ids, limit + 1),
        limit);
  }
  if (action == "read.dividends") {
    Require(tx, actor, r.organization_id(), "exchange.dividends.manage");
    const auto rows = sql::Exec(
        tx,
        "SELECT d.id::text FROM finance.exchange_dividends d JOIN finance.exchange_stocks s ON s.id=d.stock_id WHERE "
        "s.organization_id=$1::uuid AND ($2='' OR d.id>NULLIF($2,'')::uuid) ORDER BY d.id LIMIT $3",
        r.organization_id(), cursor, limit + 1);
    std::string out = "{\"items\":[";
    int i = 0;
    for (const auto& row : rows) {
      if (i == limit) break;
      if (i++) out += ',';
      out += Dividend(tx, row[0].c_str());
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
  Account(tx, r, account, "exchange.trade", false);
  if (action == "read.history")
    return Page(sql::Exec(tx,
                          "SELECT "
                          "t.sequence::text,(to_jsonb(t)||jsonb_build_object('ticker',s.ticker,'price',price::text,'"
                          "total',total::text,'fee',fee::text,'side',CASE WHEN buyer_account_id=$1::uuid THEN 'BUY' "
                          "ELSE 'SELL' END))::text FROM finance.exchange_trades t JOIN finance.exchange_stocks s ON "
                          "s.id=t.stock_id WHERE (buyer_account_id=$1::uuid OR seller_account_id=$1::uuid) AND ($2='' "
                          "OR t.sequence<NULLIF($2,'')::bigint) ORDER BY t.sequence DESC LIMIT $3",
                          account, cursor, limit + 1),
                limit);
  if (action == "read.portfolio")
    return Page(
        sql::Exec(
            tx,
            R"SQL(SELECT p.stock_id::text,jsonb_build_object('stock_id',s.id,'ticker',s.ticker,'name',s.name,'status',s.status,'account_id',p.account_id,
    'quantity',p.quantity,'reserved',p.reserved,'available',p.quantity-p.reserved,'average_price',CASE WHEN p.quantity=0 THEN '0.00' ELSE (p.cost_cents/100/p.quantity)::numeric(38,2)::text END,
    'last_price',t.price::text,'last_trade_at',t.created_at,'estimated_value',(t.price*p.quantity)::numeric(38,2)::text,
    'unrealized',(t.price*p.quantity-p.cost_cents/100)::numeric(38,2)::text,'realized',(p.realized_cents/100)::numeric(38,2)::text,
    'dividends',(p.dividends_cents/100)::numeric(38,2)::text)::text
    FROM finance.exchange_positions p JOIN finance.exchange_stocks s ON s.id=p.stock_id
    LEFT JOIN LATERAL(SELECT price,created_at FROM finance.exchange_trades WHERE stock_id=s.id ORDER BY sequence DESC LIMIT 1) t ON true
    WHERE account_id=$1::uuid AND ($2='' OR p.stock_id>NULLIF($2,'')::uuid) ORDER BY p.stock_id LIMIT $3)SQL",
            account, cursor, limit + 1),
        limit);
  if (action == "read.dividend_history")
    return Page(sql::Exec(tx,
                          "SELECT "
                          "i.id::text,(to_jsonb(i)||jsonb_build_object('amount',i.amount::text,'ticker',s.ticker,'pay_"
                          "at',d.pay_at))::text FROM finance.exchange_distributions i JOIN finance.exchange_dividends "
                          "d ON d.id=i.dividend_id JOIN finance.exchange_stocks s ON s.id=d.stock_id WHERE "
                          "i.account_id=$1::uuid AND ($2='' OR i.id>NULLIF($2,'')::uuid) ORDER BY i.id LIMIT $3",
                          account, cursor, limit + 1),
                limit);
  throw std::invalid_argument("unknown exchange query");
}
}  // namespace horizon::exchange

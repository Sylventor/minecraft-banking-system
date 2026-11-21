#include <algorithm>
#include <vector>

#include "horizon/business.hpp"
#include "horizon/business_internal.hpp"

namespace horizon::business {
namespace {
const std::string kBase = R"SQL(
WITH params AS (SELECT $1::uuid org,$2::text ids,$3::timestamptz starts,$4::timestamptz ends,
  $5::text tz,$6::text step,$7::text category,$8::int lim,$9::text cur),
postings AS (
 SELECT o.id,o.operation_type type,o.status,o.created_at,o.source_account_id,o.destination_account_id,o.reason,
   sum(CASE e.direction WHEN 'credit' THEN e.amount ELSE -e.amount END) net,
   COALESCE(c.category_key,'other') category
 FROM finance.ledger_entries e JOIN finance.ledger_transactions l ON l.id=e.transaction_id
 JOIN finance.financial_operations o ON o.id=l.operation_id CROSS JOIN params p
 LEFT JOIN finance.operation_categories c ON c.operation_id=o.id AND c.organization_id=p.org
 WHERE e.account_id=ANY(string_to_array(p.ids,',')::uuid[])
 GROUP BY o.id,c.category_key
), flows AS (SELECT x.* FROM postings x CROSS JOIN params p WHERE x.net<>0 AND x.created_at>=p.starts AND x.created_at<p.ends
 AND (p.category='' OR x.category=p.category))
)SQL";
std::string MetricSql() {
  return R"SQL(SELECT jsonb_build_object(
 'incoming',COALESCE(sum(net) FILTER(WHERE net>0),0)::numeric(38,2)::text,
 'outgoing',COALESCE(-sum(net) FILTER(WHERE net<0),0)::numeric(38,2)::text,
 'net_cashflow',COALESCE(sum(net),0)::numeric(38,2)::text,
 'incoming_count',count(*) FILTER(WHERE net>0),'outgoing_count',count(*) FILTER(WHERE net<0),
 'average_incoming',COALESCE(avg(net) FILTER(WHERE net>0),0)::numeric(38,2)::text,
 'average_outgoing',COALESCE(avg(-net) FILTER(WHERE net<0),0)::numeric(38,2)::text,
 'largest_incoming',COALESCE(max(net) FILTER(WHERE net>0),0)::numeric(38,2)::text,
 'largest_outgoing',COALESCE(max(-net) FILTER(WHERE net<0),0)::numeric(38,2)::text)::text FROM flows)SQL";
}
}  // namespace
std::string Analytics(const std::string& database, const banking::v1::BusinessRequest& r) {
  pqxx::connection db{database};
  pqxx::read_transaction tx{db};
  tx.exec("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ");
  const auto actor = r.context().actor_id(), org = r.organization_id();
  // The read transaction uses a non-locking permission snapshot; writes recheck with row locks.
  if (!sql::One(tx,
                "SELECT EXISTS(SELECT 1 FROM finance.organization_members WHERE organization_id=$1::uuid "
                "AND principal_id=$2::uuid AND revoked_at IS NULL AND 'analytics.read'=ANY(permissions))",
                org, actor)[0]
           .as<bool>())
    throw DomainError(ErrorCode::kDenied, "analytics permission is not granted");
  const auto data = r.payload().empty() ? "{}" : r.payload();
  const auto account = Field(tx, data, "account_id");
  if (!account.empty()) Id(account);
  std::string allowed;
  for (const auto& id : r.allowed_account_ids()) {
    Id(id);
    if (!allowed.empty()) allowed += ',';
    allowed += id;
  }
  const auto rows = sql::Exec(
      tx,
      "SELECT a.id::text FROM finance.accounts a WHERE organization_id=$1::uuid "
      "AND ($3='' OR a.id=NULLIF($3,'')::uuid) AND (NOT $4::boolean OR a.id=ANY(string_to_array($5,',')::uuid[])) "
      "AND (SELECT count(DISTINCT permission) FROM finance.account_permissions p WHERE p.account_id=a.id "
      "AND p.principal_id=$2::uuid AND p.revoked_at IS NULL AND p.permission IN "
      "('accounts.read','balance.read','history.read'))=3 ORDER BY a.id",
      org, actor, account, r.resource_scoped(), allowed);
  if (rows.empty()) throw DomainError(ErrorCode::kDenied, "no authorized organization accounts");
  std::string ids;
  for (const auto& row : rows) {
    if (!ids.empty()) ids += ',';
    ids += row[0].c_str();
  }
  const auto from = Field(tx, data, "from"), to = Field(tx, data, "to"), tz = Field(tx, data, "timezone", "UTC"),
             interval = Field(tx, data, "interval", "day"), category = Field(tx, data, "category");
  if (interval != "day" && interval != "week" && interval != "month") throw std::invalid_argument("invalid interval");
  if (!sql::One(tx, "SELECT $2::timestamptz>$1::timestamptz AND $2::timestamptz-$1::timestamptz<=interval '370 days'",
                from, to)[0]
           .as<bool>())
    throw std::invalid_argument("invalid analytics period");
  const auto limit = std::clamp(std::stoi(Field(tx, data, "limit", "25")), 1, 100);
  const auto cursor = Field(tx, data, "cursor");
  if (!cursor.empty()) Id(cursor);
  auto run = [&](const std::string& sql, const std::string& start, const std::string& end) {
    return horizon::sql::One(tx, kBase + sql, org, ids, start, end, tz, interval, category, limit, cursor)[0]
        .as<std::string>();
  };
  if (r.action() == "analytics.summary") {
    const auto current = run(MetricSql(), from, to);
    const auto previous = run(MetricSql(), Field(tx, data, "previous_from"), Field(tx, data, "previous_to"));
    return sql::One(tx, R"SQL(SELECT jsonb_build_object('current',$1::jsonb,'previous',$2::jsonb,
    'balance',(SELECT COALESCE(sum(balance),0)::numeric(38,2)::text FROM finance.accounts WHERE id=ANY(string_to_array($3,',')::uuid[])),
    'held',(SELECT COALESCE(sum(held),0)::numeric(38,2)::text FROM finance.accounts WHERE id=ANY(string_to_array($3,',')::uuid[])),
    'changes',(SELECT jsonb_object_agg(k,jsonb_build_object('delta',((($1::jsonb->>k)::numeric-($2::jsonb->>k)::numeric)::numeric(38,2))::text,
      'percent',CASE WHEN ($2::jsonb->>k)::numeric=0 THEN NULL ELSE round(100*(($1::jsonb->>k)::numeric-($2::jsonb->>k)::numeric)/abs(($2::jsonb->>k)::numeric),2)::text END))
      FROM jsonb_object_keys($1::jsonb) k),'account_count',$4::int,'currency','DB')::text)SQL",
                    current, previous, ids, rows.size())[0]
        .as<std::string>();
  }
  if (r.action() == "analytics.cashflow" || r.action() == "analytics.balance-history") {
    const std::string query = R"SQL(, buckets AS (SELECT generate_series(date_trunc(p.step,p.starts AT TIME ZONE p.tz),
       date_trunc(p.step,(p.ends-interval '1 microsecond') AT TIME ZONE p.tz),('1 '||p.step)::interval) local_at FROM params p),
     grouped AS (SELECT date_trunc(p.step,x.created_at AT TIME ZONE p.tz) local_at,
       COALESCE(sum(x.net) FILTER(WHERE x.net>0 AND (p.category='' OR x.category=p.category)),0) incoming,
       COALESCE(-sum(x.net) FILTER(WHERE x.net<0 AND (p.category='' OR x.category=p.category)),0) outgoing,
       count(*) FILTER(WHERE x.net<>0 AND (p.category='' OR x.category=p.category)) activity,sum(x.net) movement
       FROM postings x CROSS JOIN params p WHERE x.created_at>=p.starts AND x.created_at<p.ends GROUP BY 1),
     points AS (SELECT (b.local_at AT TIME ZONE p.tz) at,COALESCE(g.incoming,0)::numeric(38,2)::text incoming,
       COALESCE(g.outgoing,0)::numeric(38,2)::text outgoing,COALESCE(g.activity,0) activity,
       ((SELECT COALESCE(sum(net),0) FROM postings WHERE created_at<p.starts)+
         sum(COALESCE(g.movement,0)) OVER(ORDER BY b.local_at))::numeric(38,2)::text balance
       FROM buckets b CROSS JOIN params p LEFT JOIN grouped g ON g.local_at=b.local_at)
     SELECT jsonb_build_object('items',COALESCE(jsonb_agg(to_jsonb(points) ORDER BY at),'[]'::jsonb),'currency','DB')::text FROM points)SQL";
    return run(query, from, to);
  }
  if (r.action() == "analytics.categories")
    return run(R"SQL(SELECT jsonb_build_object('items',COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb))::text
   FROM (SELECT c.key,c.name,COALESCE(sum(f.net) FILTER(WHERE f.net>0),0)::numeric(38,2)::text incoming,
     COALESCE(-sum(f.net) FILTER(WHERE f.net<0),0)::numeric(38,2)::text outgoing,count(f.id) count
     FROM finance.business_categories c CROSS JOIN params p LEFT JOIN flows f ON f.category=c.key
     WHERE c.organization_id=p.org GROUP BY c.key,c.name ORDER BY c.name) x)SQL",
               from, to);
  if (r.action() == "analytics.counterparties")
    return run(R"SQL(SELECT jsonb_build_object('items',COALESCE(jsonb_agg(to_jsonb(x)),'[]'::jsonb))::text FROM
    (SELECT a.public_number,COALESCE(o.name,a.public_number,'Banking System') name,CASE WHEN f.net>0 THEN 'incoming' ELSE 'outgoing' END direction,
    sum(abs(f.net))::numeric(38,2)::text volume,count(*) count,max(f.created_at) last_operation
    FROM flows f LEFT JOIN finance.accounts a ON a.id=CASE WHEN f.net>0 THEN f.source_account_id ELSE f.destination_account_id END
    LEFT JOIN finance.organizations o ON o.id=a.organization_id GROUP BY a.public_number,o.name,(f.net>0)
    ORDER BY sum(abs(f.net)) DESC LIMIT (SELECT lim FROM params)) x)SQL",
               from, to);
  if (r.action() == "analytics.transactions")
    return run(R"SQL(, page AS (SELECT x.id,x.type,x.status,x.created_at,x.reason,x.category,
     x.net::numeric(38,2)::text amount,CASE WHEN net>0 THEN 'incoming' WHEN net<0 THEN 'outgoing' ELSE 'internal' END direction
     FROM postings x CROSS JOIN params p WHERE x.created_at>=p.starts AND x.created_at<p.ends AND (p.category='' OR x.category=p.category)
     AND (p.cur='' OR (x.created_at,x.id)<(SELECT created_at,id FROM postings WHERE id=NULLIF(p.cur,'')::uuid))
     ORDER BY x.created_at DESC,x.id DESC LIMIT (SELECT lim+1 FROM params)), first_page AS
     (SELECT * FROM page ORDER BY created_at DESC,id DESC LIMIT (SELECT lim FROM params))
     SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at DESC,x.id DESC) FROM first_page x),'[]'::jsonb),
       'next_cursor',CASE WHEN (SELECT count(*) FROM page)>(SELECT lim FROM params) THEN
       (SELECT id FROM first_page ORDER BY created_at,id LIMIT 1) ELSE NULL END)::text)SQL",
               from, to);
  if (r.action() == "analytics.payroll") {
    if (!sql::One(tx,
                  "SELECT EXISTS(SELECT 1 FROM finance.organization_members WHERE organization_id=$1::uuid AND "
                  "principal_id=$2::uuid AND revoked_at IS NULL AND 'payroll.read'=ANY(permissions))",
                  org, actor)[0]
             .as<bool>())
      throw DomainError(ErrorCode::kDenied, "payroll read permission is not granted");
    return run(R"SQL(, page AS (SELECT i.id,i.batch_id,i.user_id,i.amount::text amount,i.status,i.operation_id,i.paid_at
      FROM finance.payroll_items i JOIN finance.payroll_batches b ON b.id=i.batch_id CROSS JOIN params p
      WHERE b.organization_id=p.org AND b.source_account_id=ANY(string_to_array(p.ids,',')::uuid[]) AND i.status='completed'
      AND i.paid_at>=p.starts AND i.paid_at<p.ends AND (p.category='' OR p.category='payroll')
      AND (p.cur='' OR i.id>NULLIF(p.cur,'')::uuid) ORDER BY i.id LIMIT (SELECT lim+1 FROM params)),
      first_page AS (SELECT * FROM page ORDER BY id LIMIT (SELECT lim FROM params))
      SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.id) FROM first_page x),'[]'::jsonb),
      'next_cursor',CASE WHEN (SELECT count(*) FROM page)>(SELECT lim FROM params) THEN (SELECT id FROM first_page ORDER BY id DESC LIMIT 1) ELSE NULL END)::text)SQL",
               from, to);
  }
  throw std::invalid_argument("unknown analytics action");
}
}  // namespace horizon::business

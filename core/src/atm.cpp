#include "horizon/atm.hpp"

#include <pqxx/pqxx>
#include <regex>

#include "horizon/error.hpp"
#include "horizon/hpay.hpp"
#include "horizon/postgres_ledger.hpp"
#include "horizon/sql.hpp"

namespace horizon {
namespace {
constexpr const char* kService = "00000000-0000-0000-0000-000000000027";
std::string Field(pqxx::work& tx, const std::string& json, const std::string& key) {
  return sql::One(tx, "SELECT COALESCE($1::jsonb->>$2,'')", json, key)[0].as<std::string>();
}
void Need(bool ok, const std::string& message) {
  if (!ok) throw DomainError(ErrorCode::kConflict, message);
}
void Id(const std::string& s) {
  static const std::regex uuid("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$");
  if (!std::regex_match(s, uuid)) throw std::invalid_argument("canonical UUID required");
}
void Event(pqxx::work& tx, const std::string& actor, const std::string& device, const std::string& id,
           const std::string& action, const std::string& details = "{}") {
  sql::Exec(tx,
            "INSERT INTO finance.atm_events(actor_id,device_id,operation_id,action,details) "
            "VALUES($1::uuid,NULLIF($2,''),NULLIF($3,'')::uuid,$4,$5::jsonb)",
            actor, device, id, action, details);
}
std::string View(pqxx::work& tx, const std::string& id) {
  return sql::One(tx, "SELECT row_to_json(o)::text FROM finance.atm_operations o WHERE id=$1::uuid", id)[0]
      .as<std::string>();
}
std::string Primary(pqxx::work& tx, const std::string& user) {
  Id(user);
  const auto rows =
      sql::Exec(tx,
                "SELECT a.id::text FROM finance.primary_accounts p JOIN finance.accounts a ON a.id=p.account_id "
                "WHERE p.principal_id=$1::uuid AND a.owner_principal_id=$1::uuid AND a.account_type='personal' AND "
                "a.status='active'",
                user);
  Need(!rows.empty(), "Основной личный счёт недоступен. Откройте личный кабинет банка.");
  return rows[0][0].as<std::string>();
}
TransferCommand Command(const banking::v1::BusinessRequest& r, const std::string& user, const std::string& id,
                        const std::string& phase, const std::string& account, const std::string& amount,
                        const std::string& destination = {}) {
  return {user,
          account,
          destination,
          "atm:" + id + ":" + phase,
          std::string(64, '0'),
          "Банкомат: " + phase + " / " + id,
          "physical-atm:" + id,
          r.context().request_id(),
          r.context().correlation_id(),
          "",
          Money::Parse(amount),
          "atm"};
}
void Settle(pqxx::work& tx, const banking::v1::BusinessRequest& r, const pqxx::row& op, bool cancel) {
  // Columns: id,user,device,kind,quantity,account,hold,status,instance,evidence.
  const auto id = op[0].as<std::string>(), user = op[1].as<std::string>(), kind = op[3].as<std::string>();
  std::string financial;
  if (kind != "collection" && (!cancel || kind == "withdraw")) {
    const auto system =
        sql::One(tx, "SELECT id::text FROM finance.accounts WHERE account_type='system'")[0].as<std::string>();
    const std::string phase = cancel ? "cancel" : kind;
    auto cmd = Command(r, user, id, phase, kind == "deposit" ? system : op[5].as<std::string>(),
                       op[4].as<std::string>() + ".00",
                       cancel              ? ""
                       : kind == "deposit" ? op[5].as<std::string>()
                                           : system);
    const auto result = PostgresLedger::AtmSettlement(tx, cmd, phase, op[6].as<std::string>());
    financial = result.id;
  }
  sql::Exec(
      tx,
      "UPDATE finance.atm_operations SET status=$2,operation_id=NULLIF($3,'')::uuid,updated_at=now() WHERE id=$1::uuid",
      id, cancel ? "FAILED" : "COMPLETED", financial);
}
constexpr const char* kOp =
    "SELECT id::text,user_id::text,device_id,kind,quantity::text,COALESCE(account_id::text,''),"
    "COALESCE(hold_id::text,''),status,instance_id::text,evidence::text FROM finance.atm_operations ";
}  // namespace

std::string Atm::Execute(const std::string& database, const banking::v1::BusinessRequest& r) {
  if (r.context().actor_id() != kService || r.context().source() != "atm")
    throw DomainError(ErrorCode::kDenied, "dedicated ATM service context required");
  if (r.payload().size() > 262144) throw std::invalid_argument("ATM payload too large");
  pqxx::connection db{database};
  pqxx::work tx{db};
  const auto get = [&](const std::string& key) { return Field(tx, r.payload(), key); };
  const auto action = r.action();
  const bool purchase = action == "hpay.quote" || action == "hpay.confirm" || action == "hpay.cancel";
  if (action == "prepare" || action == "balance" || action == "transfer" || purchase) {
    sql::Exec(tx, "SELECT pg_advisory_xact_lock_shared(hashtextextended($1,0))", "privacy:" + get("user_id"));
    if (!sql::Exec(tx, "SELECT user_id FROM finance.privacy_subjects WHERE user_id=$1::uuid", get("user_id")).empty())
      throw DomainError(ErrorCode::kDenied, "profile processing is restricted");
  }
  if (action == "status") {
    const auto json =
        sql::One(
            tx,
            "SELECT jsonb_build_object('available',EXISTS(SELECT 1 FROM finance.atm_devices d "
            "JOIN finance.atm_runtime r ON r.instance_id=d.instance_id WHERE r.expires_at>now() AND d.is_master AND "
            "d.connected "
            "AND d.last_seen_at>now()-interval '15 seconds'),'connected',(SELECT count(*) FROM finance.atm_devices "
            "WHERE connected AND last_seen_at>now()-interval '15 seconds'))::text")[0]
            .as<std::string>();
    tx.commit();
    return json;
  }
  if (action == "admin.list") {
    const auto json = sql::One(tx, R"SQL(SELECT jsonb_build_object(
      'devices',(SELECT COALESCE(jsonb_agg(to_jsonb(d)||jsonb_build_object('current_operation',
        (SELECT id FROM finance.atm_operations o WHERE o.device_id=d.id AND o.status NOT IN ('COMPLETED','FAILED')))
        ORDER BY d.id),'[]') FROM finance.atm_devices d),
      'operations',(SELECT COALESCE(jsonb_agg(to_jsonb(o) ORDER BY o.created_at DESC,o.id DESC),'[]') FROM
         (SELECT * FROM finance.atm_operations WHERE ($1='' OR device_id=$1)
          AND ($2='' OR status=$2) ORDER BY created_at DESC,id DESC LIMIT 50 OFFSET $3::int) o),
      'events',(SELECT COALESCE(jsonb_agg(to_jsonb(e) ORDER BY e.id DESC),'[]') FROM
         (SELECT * FROM finance.atm_events WHERE ($1='' OR device_id=$1) ORDER BY id DESC LIMIT 100) e))::text)SQL",
                               get("device_id"), get("status"), get("offset"))[0]
                          .as<std::string>();
    tx.commit();
    return json;
  }
  if (action == "admin.enabled") {
    Need(get("enabled") == "true" || get("enabled") == "false", "Некорректная доступность");
    const auto changed = sql::Exec(tx, "UPDATE finance.atm_devices SET enabled=$2::boolean WHERE id=$1 RETURNING id",
                                   r.target_id(), get("enabled"));
    Need(!changed.empty(), "Банкомат не найден");
    Event(tx, get("admin_id"), r.target_id(), "", "availability", r.payload());
    tx.commit();
    return "{}";
  }
  if (action == "admin.resolve") {
    Id(r.target_id());
    Id(get("admin_id"));
    Need(get("reason").size() >= 5 && get("reason").size() <= 1000, "Укажите основание ручной сверки");
    const auto rows = sql::Exec(tx, std::string(kOp) + "WHERE id=$1::uuid FOR UPDATE", r.target_id());
    Need(!rows.empty(), "Операция не найдена");
    Need(rows[0][7].as<std::string>() == "REQUIRES_REVIEW", "Операция не ожидает ручной сверки");
    Need(get("decision") == "settle" || get("decision") == "cancel", "Некорректное решение");
    Settle(tx, r, rows[0], get("decision") == "cancel");
    Event(tx, get("admin_id"), rows[0][2].c_str(), r.target_id(), "manual." + get("decision"), r.payload());
    const auto json = View(tx, r.target_id());
    tx.commit();
    return json;
  }
  const auto instance = get("instance_id");
  Id(instance);
  sql::Exec(tx, "SELECT pg_advisory_xact_lock(220028)");
  if (action == "lease") {
    const auto lease = sql::Exec(tx, "SELECT instance_id::text,expires_at>now() FROM finance.atm_runtime FOR UPDATE");
    Need(lease.empty() || lease[0][0].as<std::string>() == instance || !lease[0][1].as<bool>(),
         "Уже работает другой координатор банкоматов");
    if (lease.empty() || lease[0][0].as<std::string>() != instance || !lease[0][1].as<bool>()) {
      const auto pending =
          sql::Exec(tx, std::string(kOp) + "WHERE status NOT IN ('COMPLETED','FAILED','REQUIRES_REVIEW') FOR UPDATE");
      for (const auto& op : pending) {
        if (op[7].as<std::string>() == "PROCESSING") {
          sql::Exec(tx,
                    "UPDATE finance.atm_operations SET status='REQUIRES_REVIEW',error='Координатор отключился во время "
                    "физической операции',updated_at=now() WHERE id=$1::uuid",
                    op[0].c_str());
        } else {
          Settle(tx, r, op, true);
        }
        Event(tx, kService, op[2].c_str(), op[0].c_str(), "recovery");
      }
      sql::Exec(tx, "UPDATE finance.atm_devices SET connected=false");
    }
    sql::Exec(tx,
              "INSERT INTO finance.atm_runtime(id,instance_id,expires_at,max_amount,player_distance) "
              "VALUES(true,$1::uuid,now()+interval '45 seconds',$2::int,$3::numeric) ON CONFLICT(id) DO UPDATE SET "
              "instance_id=EXCLUDED.instance_id,expires_at=EXCLUDED.expires_at,max_amount=EXCLUDED.max_amount,player_"
              "distance=EXCLUDED.player_distance",
              instance, get("max_amount"), get("player_distance"));
    tx.commit();
    return "{}";
  }
  Need(!sql::Exec(tx, "SELECT id FROM finance.atm_runtime WHERE instance_id=$1::uuid AND expires_at>now() FOR UPDATE",
                  instance)
            .empty(),
       "Координатор потерял действующую аренду; операции остановлены");
  if (purchase) {
    const auto result = hpay::Purchase(tx, r);
    tx.commit();
    return result;
  }
  if (action == "heartbeat") {
    sql::Exec(tx, "UPDATE finance.atm_runtime SET expires_at=now()+interval '45 seconds'");
    const auto devices =
        sql::Exec(tx, "SELECT value::text FROM jsonb_array_elements($1::jsonb->'devices')", r.payload());
    for (const auto& row : devices) {
      const auto json = row[0].as<std::string>();
      if (!sql::One(tx,
                    "SELECT EXISTS(SELECT 1 FROM finance.atm_devices WHERE id=$1::jsonb->>'id' AND "
                    "connected=($1::jsonb->>'connected')::boolean AND error=$1::jsonb->>'error')",
                    json)[0]
               .as<bool>())
        Event(tx, kService, Field(tx, json, "id"), "", "device.status",
              sql::One(tx, "SELECT ($1::jsonb - 'observations' - 'container')::text", json)[0].as<std::string>());
      sql::Exec(tx,
                R"SQL(INSERT INTO finance.atm_devices(id,username,is_master,instance_id,connected,dimension,position,
        observations,inventory,free_space,error,container) SELECT j->>'id',j->>'username',(j->>'master')::boolean,$2::uuid,
        (j->>'connected')::boolean,j->>'dimension',j->'position',j->'observations',(j->>'inventory')::int,
        (j->>'free_space')::int,left(j->>'error',500),j->'container' FROM (SELECT $1::jsonb j) t
        ON CONFLICT(id) DO UPDATE SET username=EXCLUDED.username,is_master=EXCLUDED.is_master,
        instance_id=EXCLUDED.instance_id,connected=EXCLUDED.connected,dimension=EXCLUDED.dimension,position=EXCLUDED.position,
        observations=EXCLUDED.observations,inventory=EXCLUDED.inventory,free_space=EXCLUDED.free_space,
        error=EXCLUDED.error,container=EXCLUDED.container,last_seen_at=now())SQL",
                json, instance);
    }
    const auto unstarted = sql::Exec(
        tx, std::string(kOp) +
                "WHERE status IN ('CREATED','RESERVED') AND updated_at<now()-interval '45 seconds' FOR UPDATE");
    for (const auto& op : unstarted) {
      Settle(tx, r, op, true);
      Event(tx, kService, op[2].c_str(), op[0].c_str(), "preflight.timeout");
    }
    const auto stale = sql::Exec(
        tx, std::string(kOp) + "WHERE status='PROCESSING' AND updated_at<now()-interval '90 seconds' FOR UPDATE");
    for (const auto& op : stale) {
      sql::Exec(tx,
                "UPDATE finance.atm_operations SET status='REQUIRES_REVIEW',error='Истёк срок физического "
                "подтверждения',updated_at=now() WHERE id=$1::uuid",
                op[0].c_str());
      Event(tx, kService, op[2].c_str(), op[0].c_str(), "physical.timeout");
    }
    const auto blocked = sql::One(tx,
                                  "SELECT COALESCE(jsonb_agg(device_id),'[]')::text FROM finance.atm_operations WHERE "
                                  "status NOT IN ('COMPLETED','FAILED')")[0]
                             .as<std::string>();
    tx.commit();
    return "{\"blocked\":" + blocked + "}";
  }
  if (action == "balance" || action == "transfer") {
    const auto account = Primary(tx, get("user_id"));
    if (action == "balance") {
      Need(sql::One(
               tx,
               "SELECT count(*)=2 FROM finance.account_permissions WHERE account_id=$1::uuid "
               "AND principal_id=$2::uuid AND permission IN ('accounts.read','balance.read') AND revoked_at IS NULL",
               account, get("user_id"))[0]
               .as<bool>(),
           "Нет права на просмотр баланса");
      const auto json =
          sql::One(tx,
                   "SELECT jsonb_build_object('balance',balance::text,'available',(balance-held)::text)::text FROM "
                   "finance.accounts WHERE id=$1::uuid",
                   account)[0]
              .as<std::string>();
      tx.commit();
      return json;
    }
    Id(get("request_key"));
    auto cmd = Command(r, get("user_id"), get("request_key"), "transfer", account, get("amount"),
                       Primary(tx, get("recipient_id")));
    const auto result = PostgresLedger::AtmSettlement(tx, cmd, "transfer");
    Event(tx, get("user_id"), "", "", "player.transfer", "{\"operation_id\":\"" + result.id + "\"}");
    tx.commit();
    return "{\"operation_id\":\"" + result.id + "\"}";
  }
  if (action == "prepare") {
    Id(get("request_key"));
    Id(get("user_id"));
    Id(get("player_uuid"));
    const auto existing = sql::Exec(tx, std::string(kOp) + "WHERE request_key=$1::uuid", get("request_key"));
    if (!existing.empty()) {
      Need(existing[0][1].as<std::string>() == get("user_id") && existing[0][3].as<std::string>() == get("kind") &&
               existing[0][4].as<std::string>() == get("quantity"),
           "Повторный запрос содержит другие параметры");
      const auto json = View(tx, existing[0][0].c_str());
      tx.commit();
      return json;
    }
    const auto kind = get("kind");
    Need(kind == "withdraw" || kind == "deposit" || kind == "collection", "Неизвестная операция");
    Need(sql::One(tx,
                  "SELECT $1::int BETWEEN 1 AND max_amount OR ($2='collection' AND $1::int BETWEEN 1 AND 2304) FROM "
                  "finance.atm_runtime",
                  get("quantity"), kind)[0]
             .as<bool>(),
         "Недопустимое количество дебрисов");
    // Coordinates are freshly observed by each bot, never inferred from chat/tab-list presence.
    const auto candidates = sql::Exec(tx, R"SQL(SELECT d.id FROM finance.atm_devices d CROSS JOIN finance.atm_runtime r
      CROSS JOIN LATERAL (SELECT d.observations->lower($1) p) t
      WHERE d.instance_id=$2::uuid AND d.enabled AND d.connected AND d.last_seen_at>now()-interval '10 seconds'
      AND t.p IS NOT NULL AND t.p->>'uuid'=$3 AND t.p->>'dimension'=d.dimension
      AND (t.p->>'age_ms')::int BETWEEN 0 AND 3000
      AND NOT EXISTS(SELECT 1 FROM finance.atm_operations o WHERE o.status NOT IN ('COMPLETED','FAILED') AND
        (o.device_id=d.id OR lower(o.evidence->'device_binding'->>'username')=lower(d.username)
         OR o.evidence->'device_binding'->'container'=d.container))
      AND sqrt(power((t.p->>'x')::numeric-(d.position->>'x')::numeric,2)+
               power((t.p->>'y')::numeric-(d.position->>'y')::numeric,2)+
               power((t.p->>'z')::numeric-(d.position->>'z')::numeric,2))<=r.player_distance
      ORDER BY power((t.p->>'x')::numeric-(d.position->>'x')::numeric,2)+
               power((t.p->>'y')::numeric-(d.position->>'y')::numeric,2)+
               power((t.p->>'z')::numeric-(d.position->>'z')::numeric,2),d.id LIMIT 1)SQL",
                                      get("nickname"), instance, get("player_uuid"));
    Need(!candidates.empty(), "Рядом нет свободного банкомата. Подойдите ближе и повторите команду.");
    const auto device = candidates[0][0].as<std::string>();
    // Collection is sized by the bot's live output-container capacity. The
    // remainder can stay in its inventory until the next collection command.
    if (kind == "collection")
      Need(sql::One(tx, "SELECT inventory>=$2::int FROM finance.atm_devices WHERE id=$1", device, get("quantity"))[0]
               .as<bool>(),
           "Инвентарь ближайшего банкомата изменился. Повторите инкассацию.");
    Need(
        sql::Exec(
            tx, "SELECT id FROM finance.atm_operations WHERE user_id=$1::uuid AND status NOT IN ('COMPLETED','FAILED')",
            get("user_id"))
            .empty(),
        "Предыдущая операция ещё не завершена");
    const auto id = sql::One(tx, "SELECT gen_random_uuid()::text")[0].as<std::string>();
    const auto account = kind == "collection" ? "" : Primary(tx, get("user_id"));
    std::string hold;
    if (kind == "withdraw")
      hold = PostgresLedger::AtmSettlement(
                 tx, Command(r, get("user_id"), id, "reserve", account, get("quantity") + ".00"), "reserve")
                 .hold_id;
    sql::Exec(tx,
              "INSERT INTO "
              "finance.atm_operations(id,request_key,instance_id,device_id,user_id,player_uuid,nickname,kind,quantity,"
              "account_id,hold_id,status) "
              "VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5::uuid,$6::uuid,$7,$8,$9::int,NULLIF($10,'')::uuid,NULLIF($11,'')"
              "::uuid,$12)",
              id, get("request_key"), instance, device, get("user_id"), get("player_uuid"), get("nickname"), kind,
              get("quantity"), account, hold, kind == "withdraw" ? "RESERVED" : "CREATED");
    sql::Exec(
        tx,
        "UPDATE finance.atm_operations SET evidence=jsonb_build_object('device_binding',"
        "(SELECT jsonb_build_object('username',username,'container',container) FROM finance.atm_devices WHERE id=$2)) "
        "WHERE id=$1::uuid",
        id, device);
    Event(tx, get("user_id"), device, id, "prepared");
    const auto json = View(tx, id);
    tx.commit();
    return json;
  }
  Id(r.target_id());
  const auto rows = sql::Exec(tx, std::string(kOp) + "WHERE id=$1::uuid FOR UPDATE", r.target_id());
  Need(!rows.empty(), "Операция не найдена");
  const auto op = rows[0];
  const auto status = op[7].as<std::string>();
  if (action == "get") {
    const auto json = View(tx, r.target_id());
    tx.commit();
    return json;
  }
  Need(op[8].as<std::string>() == instance, "Операция принадлежит предыдущему координатору");
  if (action == "confirm" && status == "COMPLETED") {
    const auto json = View(tx, r.target_id());
    tx.commit();
    return json;
  }
  if (action == "start") {
    Need(status == "CREATED" || status == "RESERVED", "Физическое действие уже запускалось; повтор запрещён");
    Need(!sql::Exec(
              tx, R"SQL(SELECT d.id FROM finance.atm_devices d CROSS JOIN finance.atm_runtime r
      CROSS JOIN LATERAL (SELECT d.observations->lower($2) p) t
      WHERE d.id=$1 AND d.instance_id=r.instance_id AND d.enabled AND d.connected
      AND d.last_seen_at>now()-interval '10 seconds' AND t.p->>'uuid'=$3
      AND t.p->>'dimension'=d.dimension AND (t.p->>'age_ms')::int BETWEEN 0 AND 3000
      AND sqrt(power((t.p->>'x')::numeric-(d.position->>'x')::numeric,2)+
               power((t.p->>'y')::numeric-(d.position->>'y')::numeric,2)+
               power((t.p->>'z')::numeric-(d.position->>'z')::numeric,2))<=r.player_distance)SQL",
              op[2].c_str(),
              sql::One(tx, "SELECT nickname FROM finance.atm_operations WHERE id=$1::uuid", r.target_id())[0]
                  .as<std::string>(),
              sql::One(tx, "SELECT player_uuid::text FROM finance.atm_operations WHERE id=$1::uuid", r.target_id())[0]
                  .as<std::string>())
              .empty(),
         "Игрок или банкомат больше не доступен рядом");
    sql::Exec(tx,
              "UPDATE finance.atm_operations SET status='PROCESSING',evidence=evidence||$2::jsonb,updated_at=now() "
              "WHERE id=$1::uuid",
              r.target_id(), get("evidence"));
  } else if (action == "confirm") {
    Need(status == "PROCESSING", "Операция ожидает ручной проверки");
    const auto evidence = get("evidence");
    const auto valid = sql::One(tx, R"SQL(SELECT
      ($1::jsonb->>'inventory_before')::int=(evidence->>'inventory_before')::int AND
      ($1::jsonb->>'container_before')::int=(evidence->>'container_before')::int AND
      CASE WHEN kind='deposit' THEN
        ($1::jsonb->>'inventory_after')::int-($1::jsonb->>'inventory_before')::int=quantity AND
        ($1::jsonb->>'container_before')::int-($1::jsonb->>'container_after')::int=quantity
      ELSE ($1::jsonb->>'inventory_before')::int-($1::jsonb->>'inventory_after')::int=quantity AND
        ($1::jsonb->>'container_after')::int-($1::jsonb->>'container_before')::int=quantity END
      FROM finance.atm_operations WHERE id=$2::uuid)SQL",
                                evidence, r.target_id())[0];
    Need(!valid.is_null() && valid.as<bool>(), "Физическое перемещение не подтверждено точным изменением инвентарей");
    sql::Exec(tx, "UPDATE finance.atm_operations SET evidence=evidence||$2::jsonb WHERE id=$1::uuid", r.target_id(),
              evidence);
    Settle(tx, r, op, false);
  } else if (action == "fail") {
    if (status == "COMPLETED" || status == "FAILED") {
      const auto json = View(tx, r.target_id());
      tx.commit();
      return json;
    }
    if (status == "CREATED" || status == "RESERVED")
      Settle(tx, r, op, true);
    else
      sql::Exec(tx, "UPDATE finance.atm_operations SET status='REQUIRES_REVIEW',updated_at=now() WHERE id=$1::uuid",
                r.target_id());
    sql::Exec(tx, "UPDATE finance.atm_operations SET error=left($2,500),evidence=evidence||$3::jsonb WHERE id=$1::uuid",
              r.target_id(), get("error"), get("evidence"));
  } else
    throw std::invalid_argument("unknown ATM action");
  Event(tx, op[1].c_str(), op[2].c_str(), r.target_id(), action, r.payload());
  const auto json = View(tx, r.target_id());
  tx.commit();
  return json;
}
}  // namespace horizon

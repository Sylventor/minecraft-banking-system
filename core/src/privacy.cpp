#include "horizon/privacy.hpp"

#include <pqxx/pqxx>

#include "horizon/error.hpp"
#include "horizon/sql.hpp"

namespace horizon {
std::string Privacy::Execute(const std::string& database, const banking::v1::BusinessRequest& r) {
  if (r.context().actor_id() != "00000000-0000-0000-0000-000000000028" || r.context().source() != "privacy")
    throw DomainError(ErrorCode::kDenied, "dedicated privacy service context required");
  if (r.payload().size() > 262144) throw std::invalid_argument("privacy payload too large");
  pqxx::connection db{database};
  pqxx::work tx{db};
  const auto field = [&](const std::string& key) {
    return sql::One(tx, "SELECT COALESCE($1::jsonb->>$2,'')", r.payload(), key)[0].as<std::string>();
  };
  const auto subject = field("user_id"), action = r.action();
  if (!subject.empty()) sql::One(tx, "SELECT $1::uuid", subject);
  std::string result = "{}";
  if (action == "inventory") {
    result = sql::One(tx, R"SQL(SELECT jsonb_build_object('tables',(SELECT jsonb_agg(tablename ORDER BY tablename)
      FROM pg_tables WHERE schemaname='finance' AND tablename<>'alembic_version'),
      'blockers',finance.privacy_blockers($1::uuid),
      'has_atm_storage',EXISTS(SELECT 1 FROM finance.atm_devices),
      'atm_aliases',COALESCE((SELECT jsonb_agg(DISTINCT nickname) FROM finance.atm_operations WHERE user_id=$1::uuid AND nickname<>'Deleted'),'[]'))::text)SQL",
                      subject)[0]
                 .as<std::string>();
  } else if (action == "export") {
    result = sql::One(tx, "SELECT finance.privacy_export($1::uuid,$2,$3::int)::text", subject, field("table"),
                      field("offset"))[0]
                 .as<std::string>();
  } else if (action == "hold") {
    sql::Exec(tx,
              "INSERT INTO finance.privacy_holds(user_id,expires_at,basis) VALUES($1::uuid,$2::timestamptz,$3) "
              "ON CONFLICT(user_id) DO UPDATE SET expires_at=EXCLUDED.expires_at,basis=EXCLUDED.basis",
              subject, field("expires_at"), field("basis"));
  } else if (action == "release_hold") {
    sql::Exec(tx, "DELETE FROM finance.privacy_holds WHERE user_id=$1::uuid", subject);
  } else if (action == "restrict" || action == "erase") {
    sql::Exec(tx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "privacy:" + subject);
    sql::Exec(tx, "SELECT id FROM finance.accounts WHERE owner_principal_id=$1::uuid ORDER BY id FOR UPDATE", subject);
    if (action == "erase") {
      const auto blockers =
          sql::One(tx, "SELECT finance.privacy_blockers($1::uuid)::text", subject)[0].as<std::string>();
      if (blockers != "{}") {
        result = "{\"blocked\":" + blockers + "}";
        tx.commit();
        return result;
      }
      if (field("basis").empty())
        throw std::invalid_argument("retention basis for structural financial records is required");
    }
    sql::Exec(tx,
              "INSERT INTO finance.privacy_subjects(user_id,status,retention_basis) VALUES($1::uuid,$2,$3) "
              "ON CONFLICT(user_id) DO UPDATE SET status=CASE WHEN finance.privacy_subjects.status='erased' THEN "
              "'erased' ELSE EXCLUDED.status END,"
              "retention_basis=EXCLUDED.retention_basis,updated_at=now()",
              subject, action == "erase" ? "erasing" : "restricted", field("basis"));
    sql::Exec(
        tx, "UPDATE finance.account_permissions SET revoked_at=COALESCE(revoked_at,now()) WHERE principal_id=$1::uuid",
        subject);
    sql::Exec(tx, "UPDATE finance.authorities SET revoked_at=COALESCE(revoked_at,now()) WHERE principal_id=$1::uuid",
              subject);
    sql::Exec(
        tx, "UPDATE finance.organization_members SET revoked_at=COALESCE(revoked_at,now()) WHERE principal_id=$1::uuid",
        subject);
    sql::Exec(tx,
              "UPDATE finance.payroll_schedules SET status='paused',revision=revision+1 WHERE status='active' AND "
              "(created_by=$1::uuid OR approved_by=$1::uuid)",
              subject);
    if (action == "erase") {
      result = sql::One(tx, "SELECT finance.privacy_minimize($1::uuid)::text", subject)[0].as<std::string>();
      sql::Exec(tx, "DELETE FROM finance.primary_accounts WHERE principal_id=$1::uuid", subject);
      sql::Exec(tx, "DELETE FROM finance.account_creation_keys WHERE principal_id=$1::uuid", subject);
      sql::Exec(tx,
                "UPDATE finance.accounts SET status='closed',name='Deleted',owner_principal_id=NULL WHERE "
                "owner_principal_id=$1::uuid",
                subject);
      sql::Exec(tx, "UPDATE finance.payroll_employees SET active=false,position='',note='' WHERE user_id=$1::uuid",
                subject);
      sql::Exec(tx,
                "UPDATE finance.event_outbox SET payload='{}',acknowledged_at=COALESCE(acknowledged_at,now()) WHERE "
                "principal_id=$1::uuid",
                subject);
      sql::Exec(tx, "UPDATE finance.privacy_subjects SET status='erased',updated_at=now() WHERE user_id=$1::uuid",
                subject);
    }
  } else if (action == "minimize") {
    if (field("basis").empty()) throw std::invalid_argument("reviewed retention basis required");
    result = sql::One(tx, "SELECT finance.privacy_minimize()::text")[0].as<std::string>();
  } else if (action == "erased_subjects") {
    result = sql::One(tx,
                      "SELECT jsonb_build_object('items',COALESCE(jsonb_agg(user_id),'[]'))::text FROM "
                      "finance.privacy_subjects WHERE status='erased'")[0]
                 .as<std::string>();
  } else
    throw std::invalid_argument("unknown privacy action");
  tx.commit();
  return result;
}
}  // namespace horizon

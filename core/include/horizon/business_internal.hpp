#pragma once
#include <pqxx/pqxx>
#include <regex>
#include <string>

#include "horizon/error.hpp"
#include "horizon/sql.hpp"
namespace horizon::business {
inline void Id(const std::string& v) {
  static const std::regex pattern("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$");
  if (!std::regex_match(v, pattern)) throw std::invalid_argument("canonical UUID required");
}
inline std::string Field(pqxx::transaction_base& tx, const std::string& data, const std::string& key,
                         const std::string& fallback = "") {
  return sql::One(tx, "SELECT COALESCE($1::jsonb->>$2,$3)", data, key, fallback)[0].as<std::string>();
}
inline void Require(pqxx::transaction_base& tx, const std::string& actor, const std::string& org,
                    const std::string& permission) {
  const auto rows =
      sql::Exec(tx,
                "SELECT principal_id FROM finance.organization_members WHERE organization_id=$1::uuid AND "
                "principal_id=$2::uuid AND revoked_at IS NULL AND $3=ANY(permissions) FOR SHARE",
                org, actor, permission);
  if (rows.empty()) throw DomainError(ErrorCode::kDenied, "organization permission is not granted");
}
inline bool IsOwner(pqxx::transaction_base& tx, const std::string& actor, const std::string& org) {
  return !sql::Exec(tx, "SELECT id FROM finance.organizations WHERE id=$1::uuid AND owner_id=$2::uuid FOR SHARE", org,
                    actor)
              .empty();
}
inline void Audit(pqxx::transaction_base& tx, const std::string& actor, const std::string& action,
                  const std::string& id, const std::string& data = "{}") {
  sql::Exec(tx, "SELECT finance.business_audit($1::uuid,$2,$3::uuid,$4::jsonb)", actor, action, id, data);
}
// Hold the current mandate until the financial transaction commits. Revocation
// waits for an already in-flight item, and no later item can use stale approval.
inline void RequireMandate(pqxx::transaction_base& tx, const std::string& batch, const std::string& org) {
  const auto rows = sql::Exec(tx,
                              "SELECT s.status,s.revision=b.schedule_revision AND s.approved_by=b.approved_by,"
                              "s.created_by::text,COALESCE(s.approved_by::text,'') FROM finance.payroll_schedules s "
                              "JOIN finance.payroll_batches b ON b.schedule_id=s.id WHERE b.id=$1::uuid "
                              "AND s.mode='automatic' FOR SHARE OF s",
                              batch);
  if (rows.empty()) return;
  if (rows[0][0].as<std::string>() != "active" || !rows[0][1].as<bool>(false))
    throw DomainError(ErrorCode::kDenied, "automatic mandate is paused or obsolete");
  Require(tx, rows[0][2].c_str(), org, "payroll.automatic.manage");
  Require(tx, rows[0][3].c_str(), org, "payroll.automatic.manage");
}
inline void Notice(pqxx::transaction_base& tx, const std::string& org, const std::string& kind, const std::string& id,
                   const std::string& data = "{}") {
  sql::Exec(tx, "SELECT finance.payroll_notice($1::uuid,$2,$3::uuid,$4::jsonb)", org, kind, id, data);
}
inline std::string Batch(pqxx::transaction_base& tx, const std::string& id) {
  return sql::
      One(tx,
          "SELECT "
          "((to_jsonb(b)-ARRAY['creation_key','fingerprint'])||jsonb_build_object('total',b.total::text,'fee',b.fee::"
          "text,"
          "'items',COALESCE((SELECT jsonb_agg(to_jsonb(i)||jsonb_build_object('amount',i.amount::text) ORDER BY i.id) "
          "FROM finance.payroll_items i WHERE i.batch_id=b.id),'[]'::jsonb)))::text FROM finance.payroll_batches b "
          "WHERE b.id=$1::uuid",
          id)[0]
          .as<std::string>();
}
}  // namespace horizon::business

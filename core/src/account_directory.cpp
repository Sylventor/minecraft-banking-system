#include "horizon/account_directory.hpp"

#include <algorithm>
#include <stdexcept>
#include <vector>

#include "horizon/error.hpp"
#include "horizon/money.hpp"
#include "horizon/sql.hpp"

namespace horizon {
namespace pb = banking::v1;
namespace {
const std::vector<std::string> kAll{"accounts.read",
                                    "balance.read",
                                    "history.read",
                                    "transfer",
                                    "payment.create",
                                    "hold.create",
                                    "account.manage",
                                    "payment_requests.create",
                                    "payment_requests.read",
                                    "payment_requests.cancel",
                                    "analytics.read",
                                    "analytics.export",
                                    "analytics.categorize",
                                    "payroll.read",
                                    "payroll.employees.manage",
                                    "payroll.salaries.manage",
                                    "payroll.batches.create",
                                    "payroll.approve",
                                    "payroll.execute",
                                    "payroll.automatic.manage",
                                    "exchange.trade",
                                    "exchange.issuer.manage",
                                    "exchange.dividends.manage"};
void Lock(pqxx::transaction_base& tx, const std::string& key) {
  sql::Exec(tx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", key);
}
void Event(pqxx::transaction_base& tx, const std::string& actor, const std::string& action, const std::string& id) {
  sql::Exec(tx, "INSERT INTO finance.profile_events(actor_id,action,resource_id) VALUES($1::uuid,$2,$3::uuid)", actor,
            action, id);
}
void Name(const std::string& name) {
  if (name.empty() || name.size() > 320 || name.find('\0') != std::string::npos)
    throw std::invalid_argument("invalid account name");
}
bool Manage(pqxx::transaction_base& tx, const std::string& actor, const std::string& id) {
  return !sql::Exec(tx,
                    "SELECT id FROM finance.account_permissions WHERE account_id=$1::uuid AND principal_id=$2::uuid "
                    "AND permission='account.manage' AND revoked_at IS NULL FOR SHARE",
                    id, actor)
              .empty();
}
void Grant(pqxx::transaction_base& tx, const std::string& id, const std::string& user, const std::string& by,
           const std::vector<std::string>& permissions) {
  for (const auto& permission : permissions)
    sql::Exec(tx,
              "INSERT INTO finance.account_permissions(account_id,principal_id,permission,granted_by) "
              "VALUES($1::uuid,$2::uuid,$3,$4::uuid) ON CONFLICT(account_id,principal_id,permission) DO UPDATE SET "
              "revoked_at=NULL,granted_by=EXCLUDED.granted_by",
              id, user, permission, by);
}
std::string Csv(const std::vector<std::string>& permissions) {
  std::string value;
  for (const auto& permission : permissions) {
    if (std::find(kAll.begin(), kAll.end(), permission) == kAll.end())
      throw std::invalid_argument("invalid organization permission");
    if (!value.empty()) value += ',';
    value += permission;
  }
  return value;
}
std::vector<std::string> MemberPermissions(const pb::OrganizationMember& member) {
  if (member.role() == "owner" || member.role() == "administrator") return kAll;
  if (member.role() != "member") throw std::invalid_argument("invalid organization role");
  std::vector<std::string> permissions(member.permissions().begin(), member.permissions().end());
  Csv(permissions);
  if (std::find(permissions.begin(), permissions.end(), "account.manage") != permissions.end())
    throw std::invalid_argument("member cannot manage accounts");
  return permissions;
}
std::string Create(pqxx::transaction_base& tx, const std::string& actor, const std::string& name,
                   const std::string& key, const std::string& org = "") {
  Name(name);
  const auto actual_key = key.empty() ? sql::One(tx, "SELECT gen_random_uuid()::text")[0].as<std::string>() : key;
  if (actual_key.size() > 255) throw std::invalid_argument("invalid account request key");
  Lock(tx, "account-create:" + actor + ":" + actual_key);
  const auto fingerprint = org + ":" + name;
  const auto existing = sql::Exec(tx,
                                  "SELECT account_id::text,fingerprint FROM finance.account_creation_keys WHERE "
                                  "principal_id=$1::uuid AND request_key=$2",
                                  actor, actual_key);
  if (!existing.empty()) {
    if (existing[0][1].as<std::string>() != fingerprint)
      throw DomainError(ErrorCode::kConflict, "account request key was already used");
    return existing[0][0].as<std::string>();
  }
  const auto id = sql::One(tx,
                           "INSERT INTO finance.accounts(account_type,name,owner_principal_id,organization_id) "
                           "VALUES(CASE WHEN $3='' THEN 'personal' ELSE 'organization' END,$1,CASE WHEN $3='' THEN "
                           "$2::uuid ELSE NULL END,NULLIF($3,'')::uuid) RETURNING id::text",
                           name, actor, org)[0]
                      .as<std::string>();
  if (org.empty())
    Grant(tx, id, actor, actor, kAll);
  else {
    const auto members = sql::Exec(tx,
                                   "SELECT principal_id::text,unnest(permissions) FROM finance.organization_members "
                                   "WHERE organization_id=$1::uuid AND revoked_at IS NULL",
                                   org);
    for (const auto& member : members) Grant(tx, id, member[0].c_str(), actor, {member[1].c_str()});
  }
  sql::Exec(tx,
            "INSERT INTO finance.account_creation_keys(principal_id,request_key,account_id,fingerprint) "
            "VALUES($1::uuid,$2,$3::uuid,$4)",
            actor, actual_key, id, fingerprint);
  Event(tx, actor, "account.create", id);
  return id;
}
void Fill(pqxx::transaction_base& tx, const std::string& actor, const std::string& id, pb::Account* out) {
  if (sql::Exec(tx,
                "SELECT id FROM finance.account_permissions WHERE account_id=$1::uuid AND principal_id=$2::uuid AND "
                "permission='balance.read' AND revoked_at IS NULL",
                id, actor)
          .empty())
    throw DomainError(ErrorCode::kDenied, "balance access is not granted");
  const auto row = sql::One(
      tx, "SELECT id::text,status,account_type,balance::text,held::text FROM finance.accounts WHERE id=$1::uuid", id);
  out->set_id(row[0].c_str());
  out->set_status(row[1].c_str());
  out->set_account_type(row[2].c_str());
  const auto balance = Money::Parse(row[3].c_str()), held = Money::Parse(row[4].c_str());
  out->mutable_balance()->set_amount(balance.ToString());
  out->mutable_balance()->set_currency("DB");
  out->mutable_held()->set_amount(held.ToString());
  out->mutable_held()->set_currency("DB");
  out->mutable_available()->set_amount((balance - held).ToString());
  out->mutable_available()->set_currency("DB");
  AccountDirectory::Enrich(tx, actor, out);
}
std::string OrgRole(pqxx::transaction_base& tx, const std::string& actor, const std::string& org, bool manager) {
  const auto rows = sql::Exec(tx,
                              "SELECT role FROM finance.organization_members WHERE organization_id=$1::uuid AND "
                              "principal_id=$2::uuid AND revoked_at IS NULL FOR SHARE",
                              org, actor);
  if (rows.empty() || (manager && rows[0][0].as<std::string>() == "member"))
    throw DomainError(ErrorCode::kDenied, "organization access is not granted");
  return rows[0][0].as<std::string>();
}
void FillOrg(pqxx::transaction_base& tx, const std::string& actor, const std::string& org, pb::OrganizationView* out) {
  out->set_current_role(OrgRole(tx, actor, org, false));
  const auto row =
      sql::One(tx, "SELECT id::text,name,owner_id::text FROM finance.organizations WHERE id=$1::uuid", org);
  out->set_id(row[0].c_str());
  out->set_name(row[1].c_str());
  out->set_owner_id(row[2].c_str());
  const auto members = sql::Exec(tx,
                                 "SELECT principal_id::text,role FROM finance.organization_members WHERE "
                                 "organization_id=$1::uuid AND revoked_at IS NULL ORDER BY role,principal_id",
                                 org);
  for (const auto& member : members) {
    auto* item = out->add_members();
    item->set_user_id(member[0].c_str());
    item->set_role(member[1].c_str());
    for (const auto& permission : sql::Exec(tx,
                                            "SELECT unnest(permissions) FROM finance.organization_members WHERE "
                                            "organization_id=$1::uuid AND principal_id=$2::uuid",
                                            org, member[0].as<std::string>()))
      item->add_permissions(permission[0].c_str());
  }
  const auto accounts =
      sql::Exec(tx,
                "SELECT a.id::text FROM finance.accounts a WHERE organization_id=$1::uuid AND (SELECT count(*) FROM "
                "finance.account_permissions p WHERE p.account_id=a.id AND p.principal_id=$2::uuid AND p.permission IN "
                "('accounts.read','balance.read') AND revoked_at IS NULL)=2 ORDER BY a.created_at,a.id",
                org, actor);
  for (const auto& account : accounts) Fill(tx, actor, account[0].c_str(), out->add_accounts());
}
void UpsertMember(pqxx::transaction_base& tx, const std::string& org, const pb::OrganizationMember& member) {
  const auto permissions = MemberPermissions(member);
  sql::Exec(tx,
            "INSERT INTO finance.organization_members(organization_id,principal_id,role,permissions) "
            "VALUES($1::uuid,$2::uuid,$3,string_to_array($4,',')) ON CONFLICT(organization_id,principal_id) DO UPDATE "
            "SET role=EXCLUDED.role,permissions=EXCLUDED.permissions,revoked_at=NULL",
            org, member.user_id(), member.role(), Csv(permissions));
}
}  // namespace

void AccountDirectory::Enrich(pqxx::transaction_base& tx, const std::string& actor, pb::Account* out) {
  const auto row = sql::One(tx,
                            "SELECT "
                            "public_number,name,COALESCE(owner_principal_id::text,''),COALESCE(organization_id::text,''"
                            "),EXISTS(SELECT 1 FROM finance.primary_accounts p WHERE p.principal_id=$2::uuid AND "
                            "p.account_id=a.id) FROM finance.accounts a WHERE id=$1::uuid",
                            out->id(), actor);
  out->set_public_number(row[0].c_str());
  out->set_name(row[1].c_str());
  out->set_owner_id(row[2].c_str());
  out->set_organization_id(row[3].c_str());
  out->set_is_primary(row[4].as<bool>());
  for (const auto& permission :
       sql::Exec(tx,
                 "SELECT permission FROM finance.account_permissions WHERE account_id=$1::uuid AND "
                 "principal_id=$2::uuid AND revoked_at IS NULL ORDER BY permission",
                 out->id(), actor))
    out->add_permissions(permission[0].c_str());
}
void AccountDirectory::EnrichOperation(pqxx::transaction_base& tx, pb::Operation* out) {
  const auto row = sql::One(
      tx,
      "SELECT "
      "o.reason,COALESCE(s.public_number,''),COALESCE(d.public_number,''),COALESCE(s.owner_principal_id::text,''),"
      "COALESCE(d.owner_principal_id::text,''),COALESCE(s.name,''),COALESCE(d.name,''),COALESCE(s.organization_id::"
      "text,''),COALESCE(d.organization_id::text,'') FROM finance.financial_operations o LEFT JOIN finance.accounts s "
      "ON s.id=o.source_account_id LEFT JOIN finance.accounts d ON d.id=o.destination_account_id WHERE o.id=$1::uuid",
      out->id());
  out->set_reason(row[0].c_str());
  out->set_source_public_number(row[1].c_str());
  out->set_destination_public_number(row[2].c_str());
  out->set_source_owner_id(row[3].c_str());
  out->set_destination_owner_id(row[4].c_str());
  out->set_source_name(row[5].c_str());
  out->set_destination_name(row[6].c_str());
  out->set_source_organization_id(row[7].c_str());
  out->set_destination_organization_id(row[8].c_str());
  out->mutable_fee()->set_amount("0.00");
  out->mutable_fee()->set_currency("DB");
}
void AccountDirectory::Create(const pb::CreateAccountRequest& r, pb::Account* out) {
  if (r.account_type() != "personal") throw std::invalid_argument("personal account type required");
  pqxx::connection db{database_};
  pqxx::work tx{db};
  const auto id =
      horizon::Create(tx, r.context().actor_id(), r.name().empty() ? "Личный счёт" : r.name(), r.idempotency_key());
  Fill(tx, r.context().actor_id(), id, out);
  tx.commit();
}
void AccountDirectory::EnsurePrimary(const std::string& actor, pb::Account* out) {
  pqxx::connection db{database_};
  pqxx::work tx{db};
  Lock(tx, "primary:" + actor);
  const auto current =
      sql::Exec(tx, "SELECT account_id::text FROM finance.primary_accounts WHERE principal_id=$1::uuid", actor);
  std::string id;
  if (!current.empty())
    id = current[0][0].c_str();
  else {
    const auto legacy =
        sql::Exec(tx,
                  "SELECT a.id::text FROM finance.accounts a WHERE owner_principal_id=$1::uuid AND "
                  "account_type='personal' AND status='active' AND EXISTS(SELECT 1 FROM finance.account_permissions p "
                  "WHERE p.account_id=a.id AND p.principal_id=$1::uuid AND p.permission='account.manage' AND "
                  "revoked_at IS NULL) ORDER BY a.created_at,a.id LIMIT 1",
                  actor);
    id = legacy.empty() ? horizon::Create(tx, actor, "Основной счёт", "primary-account-v1")
                        : legacy[0][0].as<std::string>();
    sql::Exec(tx, "INSERT INTO finance.primary_accounts(principal_id,account_id) VALUES($1::uuid,$2::uuid)", actor, id);
    Event(tx, actor, "account.primary", id);
  }
  Fill(tx, actor, id, out);
  tx.commit();
}
void AccountDirectory::Update(const pb::UpdateAccountRequest& r, pb::Account* out) {
  pqxx::connection db{database_};
  pqxx::work tx{db};
  const auto actor = r.context().actor_id();
  Lock(tx, "primary:" + actor);
  if (!Manage(tx, actor, r.account_id())) throw DomainError(ErrorCode::kDenied, "account management is not granted");
  const auto row = sql::One(tx,
                            "SELECT account_type,COALESCE(owner_principal_id::text,''),status FROM finance.accounts "
                            "WHERE id=$1::uuid FOR UPDATE",
                            r.account_id());
  if (!r.name().empty()) {
    Name(r.name());
    sql::Exec(tx, "UPDATE finance.accounts SET name=$2 WHERE id=$1::uuid", r.account_id(), r.name());
    Event(tx, actor, "account.rename", r.account_id());
  }
  if (r.set_primary()) {
    if (row[0].as<std::string>() != "personal" || row[1].as<std::string>() != actor ||
        row[2].as<std::string>() != "active")
      throw DomainError(ErrorCode::kDenied, "primary account must be an active owned personal account");
    sql::Exec(tx,
              "INSERT INTO finance.primary_accounts(principal_id,account_id) VALUES($1::uuid,$2::uuid) ON "
              "CONFLICT(principal_id) DO UPDATE SET account_id=EXCLUDED.account_id",
              actor, r.account_id());
    Event(tx, actor, "account.primary", r.account_id());
  }
  Fill(tx, actor, r.account_id(), out);
  tx.commit();
}
void AccountDirectory::Resolve(const pb::PublicAccountRequest& r, pb::PublicAccount* out) {
  pqxx::connection db{database_};
  pqxx::read_transaction tx{db};
  const auto rows = sql::Exec(
      tx,
      "SELECT "
      "a.id::text,a.public_number,a.name,COALESCE(a.owner_principal_id::text,''),COALESCE(a.organization_id::text,'') "
      "FROM finance.accounts a WHERE a.status='active' AND a.account_type IN ('personal','organization') AND (($1<>'' "
      "AND a.owner_principal_id=NULLIF($1,'')::uuid AND a.id=COALESCE((SELECT p.account_id FROM "
      "finance.primary_accounts p WHERE p.principal_id=NULLIF($1,'')::uuid),(SELECT legacy.id FROM finance.accounts "
      "legacy WHERE legacy.owner_principal_id=NULLIF($1,'')::uuid AND legacy.account_type='personal' AND "
      "legacy.status='active' ORDER BY legacy.created_at,legacy.id LIMIT 1))) OR ($1='' AND (a.id::text=$2 OR "
      "a.public_number=upper($2))))",
      r.owner_id(), r.identifier());
  if (rows.empty()) throw DomainError(ErrorCode::kNotFound, "recipient account is unavailable");
  const auto row = rows[0];
  out->set_account_id(row[0].c_str());
  out->set_public_number(row[1].c_str());
  out->set_name(row[2].c_str());
  out->set_owner_id(row[3].c_str());
  out->set_organization_id(row[4].c_str());
}
void AccountDirectory::EnsureOrganization(const pb::OrganizationRequest& r, pb::OrganizationView* out) {
  pqxx::connection db{database_};
  pqxx::work tx{db};
  Lock(tx, "organization:" + r.organization_id());
  if (r.owner_id() != r.context().actor_id())
    throw DomainError(ErrorCode::kDenied, "organization bootstrap requires its owner");
  const auto existing =
      sql::Exec(tx, "SELECT owner_id::text FROM finance.organizations WHERE id=$1::uuid", r.organization_id());
  if (existing.empty()) {
    sql::Exec(tx, "INSERT INTO finance.organizations(id,owner_id,name) VALUES($1::uuid,$2::uuid,$3)",
              r.organization_id(), r.owner_id(), r.name());
    for (const auto& member : r.members()) {
      if (member.role() == "owner" && member.user_id() != r.owner_id())
        throw std::invalid_argument("invalid organization owner");
      UpsertMember(tx, r.organization_id(), member);
    }
    pb::OrganizationMember owner;
    owner.set_user_id(r.owner_id());
    owner.set_role("owner");
    UpsertMember(tx, r.organization_id(), owner);
    Event(tx, r.owner_id(), "organization.bootstrap", r.organization_id());
  } else if (existing[0][0].as<std::string>() != r.owner_id())
    throw DomainError(ErrorCode::kDenied, "organization owner mismatch");
  FillOrg(tx, r.owner_id(), r.organization_id(), out);
  tx.commit();
}
void AccountDirectory::Organization(const pb::OrganizationRequest& r, pb::OrganizationView* out) {
  pqxx::connection db{database_};
  pqxx::work tx{db};
  FillOrg(tx, r.context().actor_id(), r.organization_id(), out);
  tx.commit();
}
void AccountDirectory::OrganizationAccount(const pb::OrganizationAccountRequest& r, pb::Account* out) {
  pqxx::connection db{database_};
  pqxx::work tx{db};
  Lock(tx, "organization:" + r.organization_id());
  OrgRole(tx, r.context().actor_id(), r.organization_id(), true);
  const auto id = horizon::Create(tx, r.context().actor_id(), r.name().empty() ? "Счёт организации" : r.name(),
                                  r.idempotency_key(), r.organization_id());
  Fill(tx, r.context().actor_id(), id, out);
  tx.commit();
}
void AccountDirectory::ChangeMember(const pb::OrganizationMemberRequest& r, pb::OrganizationView* out) {
  pqxx::connection db{database_};
  pqxx::work tx{db};
  Lock(tx, "organization:" + r.organization_id());
  const auto actor = r.context().actor_id();
  const auto role = OrgRole(tx, actor, r.organization_id(), true);
  const auto owner =
      sql::One(tx, "SELECT owner_id::text FROM finance.organizations WHERE id=$1::uuid", r.organization_id())[0]
          .as<std::string>();
  if (r.member().user_id() == owner || r.member().role() == "owner")
    throw DomainError(ErrorCode::kDenied, "organization owner cannot be removed or replaced");
  const auto target = sql::Exec(tx,
                                "SELECT role FROM finance.organization_members WHERE organization_id=$1::uuid AND "
                                "principal_id=$2::uuid AND revoked_at IS NULL",
                                r.organization_id(), r.member().user_id());
  if (role != "owner" &&
      (r.member().role() == "administrator" || (!target.empty() && target[0][0].as<std::string>() == "administrator")))
    throw DomainError(ErrorCode::kDenied, "only owner may change administrators");
  sql::Exec(tx,
            "UPDATE finance.account_permissions SET revoked_at=now() WHERE principal_id=$2::uuid AND account_id "
            "IN(SELECT id FROM finance.accounts WHERE organization_id=$1::uuid)",
            r.organization_id(), r.member().user_id());
  if (r.remove())
    sql::Exec(tx,
              "UPDATE finance.organization_members SET revoked_at=now() WHERE organization_id=$1::uuid AND "
              "principal_id=$2::uuid",
              r.organization_id(), r.member().user_id());
  else {
    UpsertMember(tx, r.organization_id(), r.member());
    const auto permissions = MemberPermissions(r.member());
    for (const auto& account :
         sql::Exec(tx, "SELECT id::text FROM finance.accounts WHERE organization_id=$1::uuid", r.organization_id()))
      Grant(tx, account[0].c_str(), r.member().user_id(), actor, permissions);
  }
  sql::Exec(tx,
            "INSERT INTO finance.profile_events(actor_id,action,resource_id,metadata) "
            "VALUES($1::uuid,'organization.member.change',$2::uuid,jsonb_build_object('user_id',$3::text,'role',$4::"
            "text,'removed',$5::boolean,'permissions',string_to_array($6,',')))",
            actor, r.organization_id(), r.member().user_id(), r.member().role(), r.remove(),
            Csv(MemberPermissions(r.member())));
  FillOrg(tx, actor, r.organization_id(), out);
  tx.commit();
}
}  // namespace horizon

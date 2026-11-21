#pragma once

#include <pqxx/pqxx>
#include <string>
#include <utility>

#include "horizon/banking/v1/banking.pb.h"

namespace horizon {
class AccountDirectory {
 public:
  explicit AccountDirectory(std::string database) : database_(std::move(database)) {}
  void Create(const banking::v1::CreateAccountRequest&, banking::v1::Account*);
  void EnsurePrimary(const std::string& actor, banking::v1::Account*);
  void Update(const banking::v1::UpdateAccountRequest&, banking::v1::Account*);
  void Resolve(const banking::v1::PublicAccountRequest&, banking::v1::PublicAccount*);
  void EnsureOrganization(const banking::v1::OrganizationRequest&, banking::v1::OrganizationView*);
  void Organization(const banking::v1::OrganizationRequest&, banking::v1::OrganizationView*);
  void OrganizationAccount(const banking::v1::OrganizationAccountRequest&, banking::v1::Account*);
  void ChangeMember(const banking::v1::OrganizationMemberRequest&, banking::v1::OrganizationView*);
  static void Enrich(pqxx::transaction_base&, const std::string& actor, banking::v1::Account*);
  static void EnrichOperation(pqxx::transaction_base&, banking::v1::Operation*);

 private:
  std::string database_;
};
}  // namespace horizon

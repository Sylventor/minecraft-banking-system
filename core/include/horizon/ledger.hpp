#pragma once

#include <string>

#include "horizon/money.hpp"

namespace horizon {
enum class AccountStatus { kActive, kFrozen, kClosed };
struct TransferCommand {
  std::string actor_id, source_account_id, destination_account_id, idempotency_key, fingerprint, reason,
      authority_reference, request_id, correlation_id, operation_type{"transfer"};
  Money amount;
  std::string source{"rest"};
  std::string not_after{};
};
struct OperationResult {
  std::string id, operation_type, status, source_account_id, destination_account_id, created_at, completed_at,
      related_operation_id;
  Money amount;
  std::string hold_id;
};
class LedgerRepository {
 public:
  virtual ~LedgerRepository() = default;
  virtual OperationResult Transfer(const TransferCommand& command) = 0;
  virtual OperationResult CreateHold(const TransferCommand& command) = 0;
  virtual OperationResult Refund(const TransferCommand& command, const std::string& original_operation_id) = 0;
};
}  // namespace horizon

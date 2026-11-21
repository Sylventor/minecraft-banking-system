#pragma once

#include <memory>
#include <pqxx/pqxx>
#include <string>

#include "horizon/ledger.hpp"

namespace horizon {
class PostgresLedger final : public LedgerRepository {
 public:
  explicit PostgresLedger(std::string connection_string);
  OperationResult Transfer(const TransferCommand& command) override;
  OperationResult PayRequest(TransferCommand command, const std::string& request_id);
  void PayPayrollItem(const std::string& item_id, const std::string& actor);
  static OperationResult ExchangeTransfer(pqxx::work& tx, TransferCommand command);
  static OperationResult AtmSettlement(pqxx::work& tx, TransferCommand command,
                                       const std::string& action, const std::string& hold_id = {});
  OperationResult CreateHold(const TransferCommand& command) override;
  OperationResult Refund(const TransferCommand& command, const std::string& original_operation_id) override;
  OperationResult ChangeHold(const TransferCommand& command, const std::string& hold_id, bool capture);
  [[nodiscard]] std::string connection_string() const { return connection_string_; }

 private:
  std::string connection_string_;
};
}  // namespace horizon

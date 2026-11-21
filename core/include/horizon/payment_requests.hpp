#pragma once
#include <pqxx/pqxx>
#include <string>

#include "horizon/banking/v1/banking.pb.h"

namespace horizon {
class PaymentRequests {
 public:
  static std::string Execute(const std::string& database, const banking::v1::PaymentRequestCommand& request);
};
}  // namespace horizon

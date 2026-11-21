#pragma once
#include <string>

#include "horizon/banking/v1/banking.pb.h"
namespace horizon {
class Atm {
 public:
  static std::string Execute(const std::string& database, const banking::v1::BusinessRequest& request);
};
}  // namespace horizon

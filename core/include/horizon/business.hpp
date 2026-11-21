#pragma once
#include <string>

#include "horizon/banking/v1/banking.pb.h"
namespace horizon {
namespace business {
std::string Analytics(const std::string& database, const banking::v1::BusinessRequest& request);
}
class Business {
 public:
  static std::string Execute(const std::string& database, const banking::v1::BusinessRequest& request);
  static void Process(const std::string& database, const std::string& batch, const std::string& actor);
};
}  // namespace horizon

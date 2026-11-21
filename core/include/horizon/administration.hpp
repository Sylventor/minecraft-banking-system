#pragma once
#include <string>

#include "horizon/banking/v1/banking.pb.h"

namespace horizon {
class Administration {
 public:
  static std::string Query(const std::string& database, const ::horizon::banking::v1::AdminQueryRequest& request);
  static std::string Change(const std::string& database, const ::horizon::banking::v1::AdminChangeRequest& request);
  static std::string Events(const std::string& database);
  static void Acknowledge(const std::string& database,
                          const ::horizon::banking::v1::NotificationEventsRequest& request);
};
}  // namespace horizon

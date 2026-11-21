#pragma once

#include <pqxx/pqxx>
#include <string>

#include "horizon/banking/v1/banking.pb.h"

namespace horizon::hpay {
std::string Catalogue(const std::string& database, const banking::v1::BusinessRequest& request);
std::string Purchase(pqxx::work& tx, const banking::v1::BusinessRequest& request);
}  // namespace horizon::hpay

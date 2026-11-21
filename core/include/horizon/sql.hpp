#pragma once
#include <pqxx/pqxx>
#include <string>
#include <utility>

namespace horizon::sql {
// Debian 12 ships pqxx 6.4; modern native builds use the non-deprecated 7.10 API.
template <class... Args>
pqxx::result Exec(pqxx::transaction_base& tx, const std::string& query, Args&&... args) {
#if PQXX_VERSION_MAJOR > 7 || (PQXX_VERSION_MAJOR == 7 && PQXX_VERSION_MINOR >= 10)
  return tx.exec(pqxx::zview{query.c_str()}, pqxx::params{std::forward<Args>(args)...});
#else
  return tx.exec_params(query, std::forward<Args>(args)...);
#endif
}
template <class... Args>
pqxx::row One(pqxx::transaction_base& tx, const std::string& query, Args&&... args) {
#if PQXX_VERSION_MAJOR > 7 || (PQXX_VERSION_MAJOR == 7 && PQXX_VERSION_MINOR >= 10)
  return Exec(tx, query, std::forward<Args>(args)...).one_row();
#else
  return tx.exec_params1(query, std::forward<Args>(args)...);
#endif
}
}  // namespace horizon::sql

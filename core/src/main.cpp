#include "horizon/sql.hpp"

// gRPC 1.81 includes its own deprecated TLS type in public headers on MSVC.
// Scope this vendor-header diagnostic only; handwritten code remains /W4 /WX.
#ifdef _MSC_VER
#pragma warning(push)
#pragma warning(disable : 4996)
#endif
#include <grpcpp/security/server_credentials.h>
#include <grpcpp/server_builder.h>

#include "horizon/banking/v1/banking.grpc.pb.h"
#ifdef _MSC_VER
#pragma warning(pop)
#endif
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <thread>

namespace horizon {
std::unique_ptr<::horizon::banking::v1::BankingService::Service> CreateGrpcService(const std::string&,
                                                                                   const std::string&,
                                                                                   const std::string&);
}
namespace {
volatile std::sig_atomic_t stop_requested = 0;
void Signal(int) { stop_requested = 1; }
std::string Required(const char* key) {
#ifdef _WIN32
  char* raw = nullptr;
  std::size_t length = 0;
  const auto result = _dupenv_s(&raw, &length, key);
  const std::unique_ptr<char, decltype(&std::free)> value(raw, &std::free);
  if (result != 0 || !value || length <= 1) throw std::runtime_error("required configuration missing");
  return value.get();
#else
  const char* value = std::getenv(key);
  if (value == nullptr || *value == '\0') throw std::runtime_error("required configuration missing");
  return value;
#endif
}
}  // namespace
int main() {
  try {
    const auto token = Required("INTERNAL_GRPC_TOKEN");
    if (token.size() < 32 || token.find("replace-") != std::string::npos)
      throw std::runtime_error("invalid internal service secret");
    const auto database = Required("FINANCE_DATABASE_URL");
    const auto issuance = Required("HORIZON_INITIAL_ISSUANCE_ACCOUNT_ID");
    {
      pqxx::connection db{database};
      pqxx::read_transaction tx{db};
      if (horizon::sql::One(tx, "SELECT version_num FROM finance.alembic_version")[0].as<std::string>() !=
          "finance_0012")
        throw std::runtime_error("financial migration version mismatch");
      if (!horizon::sql::One(
               tx, "SELECT EXISTS(SELECT 1 FROM finance.accounts WHERE id=$1::uuid AND account_type='system')",
               issuance)[0]
               .as<bool>())
        throw std::runtime_error("issuance account configuration mismatch");
    }
    auto service = horizon::CreateGrpcService(database, token, issuance);
    grpc::ServerBuilder builder;
    builder.SetMaxReceiveMessageSize(1024 * 1024);
    builder.AddListeningPort(Required("GRPC_BIND_ADDRESS"), grpc::InsecureServerCredentials());
    builder.RegisterService(service.get());
    auto server = builder.BuildAndStart();
    if (!server) throw std::runtime_error("gRPC startup failed");
    std::signal(SIGINT, Signal);
    std::signal(SIGTERM, Signal);
    std::jthread shutdown([&server](std::stop_token stop) {
      while (!stop.stop_requested() && !stop_requested) std::this_thread::sleep_for(std::chrono::milliseconds(50));
      if (stop_requested) server->Shutdown(std::chrono::system_clock::now() + std::chrono::seconds(5));
    });
    std::cout << "{\"service\":\"core\",\"event\":\"started\"}" << std::endl;
    server->Wait();
    shutdown.request_stop();
    std::cout << "{\"service\":\"core\",\"event\":\"stopped\"}" << std::endl;
  } catch (const std::exception&) {
    std::cerr
        << "{\"service\":\"core\",\"event\":\"startup_failed\",\"code\":\"configuration_or_database_unavailable\"}"
        << std::endl;
    return 1;
  }
}

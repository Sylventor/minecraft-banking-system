#pragma once
#include <stdexcept>
#include <string>

namespace horizon {
enum class ErrorCode { kInvalid, kNotFound, kDenied, kConflict, kInsufficient, kLimit };
class DomainError final : public std::runtime_error {
 public:
  DomainError(ErrorCode code, const std::string& message) : std::runtime_error(message), code_(code) {}
  [[nodiscard]] ErrorCode code() const noexcept { return code_; }

 private:
  ErrorCode code_;
};
}  // namespace horizon

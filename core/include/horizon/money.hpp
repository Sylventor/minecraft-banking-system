#pragma once

#include <cstdint>
#include <string>
#include <string_view>

namespace horizon {
class Money final {
 public:
  Money() noexcept = default;
  static constexpr std::int64_t kMaxCents = 999999999999999999;
  static Money Parse(std::string_view text);
  static Money FromCents(std::int64_t cents);
  [[nodiscard]] std::int64_t cents() const noexcept { return cents_; }
  [[nodiscard]] std::string ToString() const;
  friend bool operator<(Money left, Money right) noexcept { return left.cents_ < right.cents_; }
  friend bool operator<=(Money left, Money right) noexcept { return left.cents_ <= right.cents_; }
  friend bool operator==(Money left, Money right) noexcept { return left.cents_ == right.cents_; }
  friend Money operator+(Money left, Money right);
  friend Money operator-(Money left, Money right);

 private:
  explicit Money(std::int64_t cents) : cents_(cents) {}
  std::int64_t cents_{};
};
}  // namespace horizon

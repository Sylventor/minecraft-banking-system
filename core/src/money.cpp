#include "horizon/money.hpp"

#include <charconv>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace horizon {
Money Money::Parse(std::string_view text) {
  const auto dot = text.find('.');
  if (dot == std::string_view::npos || dot == 0 || dot > 16 || text.size() != dot + 3)
    throw std::invalid_argument("amount must have exactly two fractional digits");
  const auto integer = text.substr(0, dot);
  const auto fraction = text.substr(dot + 1);
  if ((integer.size() > 1 && integer.front() == '0') ||
      integer.find_first_not_of("0123456789") != std::string_view::npos ||
      fraction.find_first_not_of("0123456789") != std::string_view::npos)
    throw std::invalid_argument("invalid canonical amount");
  std::int64_t whole{};
  const auto [ptr, ec] = std::from_chars(integer.data(), integer.data() + integer.size(), whole);
  if (ec != std::errc{} || ptr != integer.data() + integer.size()) throw std::out_of_range("money overflow");
  const std::int64_t fraction_cents = (fraction[0] - '0') * 10 + (fraction[1] - '0');
  if (whole > (kMaxCents - fraction_cents) / 100) throw std::out_of_range("money overflow");
  return Money(whole * 100 + fraction_cents);
}
Money Money::FromCents(std::int64_t cents) {
  if (cents < 0) throw std::invalid_argument("negative money");
  if (cents > kMaxCents) throw std::out_of_range("money overflow");
  return Money(cents);
}
std::string Money::ToString() const {
  std::ostringstream out;
  out << cents_ / 100 << '.' << std::setw(2) << std::setfill('0') << cents_ % 100;
  return out.str();
}
Money operator+(Money left, Money right) {
  if (right.cents_ > Money::kMaxCents - left.cents_) throw std::out_of_range("money overflow");
  return Money(left.cents_ + right.cents_);
}
Money operator-(Money left, Money right) {
  if (right.cents_ > left.cents_) throw std::invalid_argument("negative money result");
  return Money(left.cents_ - right.cents_);
}
}  // namespace horizon

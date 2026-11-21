#include <gtest/gtest.h>

#include "horizon/money.hpp"

TEST(Money, ParsesAndPreservesTwoDecimals) { EXPECT_EQ(horizon::Money::Parse("125.40").ToString(), "125.40"); }
TEST(Money, RejectsAmbiguousPrecision) {
  EXPECT_THROW(horizon::Money::Parse("1.2"), std::invalid_argument);
  EXPECT_THROW(horizon::Money::Parse("1.000"), std::invalid_argument);
}
TEST(Money, CannotBecomeNegative) {
  EXPECT_THROW(horizon::Money::Parse("1.00") - horizon::Money::Parse("1.01"), std::invalid_argument);
}
TEST(Money, RejectsNonCanonicalAndNegativeAmounts) {
  for (const auto* input : {"1", "01.00", "-0.00", "-1.00", "+1.00", "1e2", "1.00 ", "nan", "1..0"}) {
    EXPECT_THROW(horizon::Money::Parse(input), std::invalid_argument) << input;
  }
}
TEST(Money, RangeMatchesEveryProtocolAndDatabase) {
  EXPECT_EQ(horizon::Money::Parse("9999999999999999.99").cents(), horizon::Money::kMaxCents);
  EXPECT_THROW(horizon::Money::Parse("9999999999999999.99") + horizon::Money::Parse("0.01"), std::out_of_range);
  EXPECT_THROW(horizon::Money::FromCents(-1), std::invalid_argument);
  EXPECT_THROW(horizon::Money::FromCents(horizon::Money::kMaxCents + 1), std::out_of_range);
}

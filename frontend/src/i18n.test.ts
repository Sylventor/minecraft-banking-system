import { describe, expect, it } from "vitest";
import { money, normalizeAmount } from "./i18n";

describe("Exact user-facing monetary formatting", () => {
  it("normalizes comma inputs without using floating point", () => {
    expect(normalizeAmount("1 250,50")).toBe("1250.50");
    expect(normalizeAmount("125,5")).toBe("125.50");
    expect(normalizeAmount("125")).toBe("125.00");
  });
  it("rejects ambiguous, negative, excessive and scientific amounts", () => {
    for (const value of [
      "1e3",
      "01.00",
      "1.001",
      "-1",
      "1,234,56",
      "0,00",
      "10000000000000000.00",
    ])
      expect(normalizeAmount(value)).toBeNull();
  });
  it("preserves the maximum supported precision in both languages", () => {
    expect(money("9999999999999999.99", "ru").replace(/\s/g, "")).toBe(
      "9999999999999999,99ДБ",
    );
    expect(money("1250.50", "en")).toBe("1,250.50 DB");
  });
});

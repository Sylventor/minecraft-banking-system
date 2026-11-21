// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { exchangeApi } from "../exchange-api";
import { TestBank, testProfile } from "../test-support";
import { IssuerExchange } from "./IssuerExchange";

vi.mock("../exchange-api", async (original) => {
  const actual = await original<typeof import("../exchange-api")>();
  return { ...actual, exchangeApi: { ...actual.exchangeApi, get: vi.fn() } };
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it.each([
  [testProfile.id, testProfile.id, true],
  ["another-owner", testProfile.id, false],
  ["another-owner", "another-author", true],
])(
  "shows dividend approval according to ownership and authorship (%s, %s)",
  async (owner, author, allowed) => {
    vi.mocked(exchangeApi.get)
      .mockResolvedValueOnce({ items: [], next_cursor: null })
      .mockResolvedValueOnce({
        items: [
          {
            id: "dividend",
            created_by: author,
            status: "draft",
            total: "10.00",
            rounding_remainder: "0.00",
            pay_at: "2030-01-01T12:00:00Z",
            items: [],
          },
        ],
        next_cursor: null,
      });
    render(
      <TestBank>
        <IssuerExchange
          organization={{ id: "org", name: "Company", owner_id: owner }}
          permissions={["exchange.dividends.manage"]}
        />
      </TestBank>,
    );
    await screen.findByText(/Остаток округления:/);
    expect(
      Boolean(
        screen.queryByRole("button", { name: "Утвердить снимок получателей" }),
      ),
    ).toBe(allowed);
  },
);

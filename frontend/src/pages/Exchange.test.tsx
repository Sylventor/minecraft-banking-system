// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Exchange, PriceChart } from "./Exchange";
import { exchangeApi, type Stock } from "../exchange-api";
import { TestBank } from "../test-support";

vi.mock("../exchange-api", () => ({
  exchangeStatus: (value: string) => value,
  exchangeApi: { get: vi.fn(), write: vi.fn() },
}));
const stock: Stock = {
  id: "stock",
  ticker: "XCON",
  name: "RP Construction",
  description: "Virtual",
  terms: "Fixed supply",
  rights: "Dividends",
  status: "active",
  total_quantity: 1000,
  last_price: "10.00",
  last_trade_at: "2026-10-24T10:00:00Z",
  change: null,
  change_percent: null,
  volume24: 20,
  trades24: 1,
  best_bid: null,
  best_ask: null,
  ipo: null,
};
beforeEach(() => {
  localStorage.clear();
  vi.mocked(exchangeApi.get).mockImplementation(
    async <T,>(path: string) =>
      (path === "stocks"
        ? { items: [stock], next_cursor: null }
        : path.endsWith("orderbook")
          ? { bids: [], asks: [] }
          : path.endsWith("trades") || path.endsWith("candles")
            ? { items: [], next_cursor: null }
            : stock) as T,
  );
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("never fabricates candles for a period without actual trades", () => {
  render(<PriceChart items={[]} />);
  expect(screen.queryByRole("img")).toBeNull();
  expect(screen.getByText(/Свечи без торгов не создаются/)).toBeTruthy();
});

it("previews the maximum cost and reuses its key after a lost response", async () => {
  vi.mocked(exchangeApi.write)
    .mockRejectedValueOnce(new TypeError("lost response"))
    .mockResolvedValueOnce({ status: "filled", id: "order" });
  render(
    <TestBank>
      <Exchange />
    </TestBank>,
  );
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: /XCON · RP Construction/ }),
  );
  const price = await screen.findByLabelText("Максимальная цена, ДБ");
  await user.clear(price);
  await user.type(price, "13.00");
  await user.clear(screen.getByLabelText("Количество"));
  await user.type(screen.getByLabelText("Количество"), "2");
  await user.click(screen.getByRole("button", { name: "Проверить заявку" }));
  expect(exchangeApi.write).not.toHaveBeenCalled();
  expect(within(screen.getByRole("dialog")).getByText(/26,00/)).toBeTruthy();
  await user.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Подтвердить",
    }),
  );
  await screen.findAllByRole("alert");
  expect(screen.queryByText(/Заявка принята/)).toBeNull();
  await user.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Подтвердить",
    }),
  );
  await screen.findByText(/Заявка принята/);
  expect(vi.mocked(exchangeApi.write).mock.calls[0]).toEqual(
    vi.mocked(exchangeApi.write).mock.calls[1],
  );
});

it("requires a cash budget for a market BUY and warns about execution price", async () => {
  render(
    <TestBank>
      <Exchange />
    </TestBank>,
  );
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: /XCON · RP Construction/ }),
  );
  await user.click(screen.getByText("Дополнительные параметры"));
  await user.selectOptions(await screen.findByLabelText("Тип"), "market");
  await user.clear(screen.getByLabelText("Максимальная цена, ДБ"));
  await user.type(screen.getByLabelText("Максимальная цена, ДБ"), "15.00");
  await user.click(screen.getByRole("button", { name: "Проверить заявку" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  await user.type(screen.getByLabelText("Максимальный бюджет, ДБ"), "20.00");
  await user.click(screen.getByRole("button", { name: "Проверить заявку" }));
  expect(
    within(screen.getByRole("dialog")).getByText(
      /Рыночная цена может отличаться/,
    ),
  ).toBeTruthy();
  expect(exchangeApi.write).not.toHaveBeenCalled();
});

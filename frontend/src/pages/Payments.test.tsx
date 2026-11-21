// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Payments } from "./Payments";
import { PendingPayments } from "../components/PendingPayments";
import { paymentApi, type PaymentRequest } from "../payment-api";
import {
  TestBank,
  testAccount,
  testDashboard,
  testProfile,
} from "../test-support";

vi.mock("../payment-api", () => ({
  paymentApi: {
    list: vi.fn(),
    get: vi.fn(),
    pay: vi.fn(),
    create: vi.fn(),
    action: vi.fn(),
  },
}));
const item: PaymentRequest = {
  id: "00000000-0000-0000-0000-000000000091",
  creator_id: "00000000-0000-0000-0000-000000000090",
  payer_id: testProfile.id,
  organization_id: "00000000-0000-0000-0000-000000000092",
  creator_name: "Shop",
  payer_name: "DemoOwner",
  merchant_name: "Магазин Пчёлка",
  recipient_account_id: "00000000-0000-0000-0000-000000000093",
  recipient_account_name: "Магазин",
  recipient_public_number: "HB-1111111111111111",
  amount: "150.00",
  currency: "DB",
  description: "Покупка мёда",
  status: "pending",
  external_id: "ORDER-123",
  metadata: {},
  created_at: "2026-10-21T12:00:00Z",
  expires_at: "2030-01-01T00:00:00Z",
  paid_at: null,
  transaction_id: null,
};
const data = {
  ...testDashboard,
  accounts: [
    {
      ...testAccount,
      permissions: [...testAccount.permissions, "payment.create"],
    },
  ],
};
beforeEach(() => {
  history.replaceState({}, "", "/payments");
  localStorage.clear();
  vi.mocked(paymentApi.list).mockResolvedValue({
    items: [item],
    next_offset: null,
  });
  vi.mocked(paymentApi.get).mockResolvedValue(item);
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  history.replaceState({}, "", "/");
});

it("does not debit before explicit confirmation and retries the same key after a network failure", async () => {
  vi.mocked(paymentApi.pay)
    .mockRejectedValueOnce(new TypeError("lost response"))
    .mockResolvedValueOnce({
      ...item,
      status: "paid",
      transaction_id: "operation",
    });
  render(
    <TestBank overrides={{ data }}>
      <Payments />
    </TestBank>,
  );
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: /Магазин Пчёлка → DemoOwner/ }),
  );
  await screen.findByRole("dialog");
  await user.selectOptions(
    screen.getByLabelText("Счёт списания"),
    testAccount.id,
  );
  await user.click(screen.getByRole("button", { name: "Оплатить" }));
  expect(paymentApi.pay).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: /Оплатить 150/ }));
  await screen.findByText(/Результат ещё не получен/);
  await user.click(screen.getByRole("button", { name: /Оплатить 150/ }));
  await screen.findByText("Оплачен", { selector: "dd" });
  expect(paymentApi.pay).toHaveBeenCalledTimes(2);
  expect(vi.mocked(paymentApi.pay).mock.calls[0]).toEqual(
    vi.mocked(paymentApi.pay).mock.calls[1],
  );
});

it("opens a direct request link and never offers payment to another user", async () => {
  history.replaceState({}, "", "/payments/" + item.id);
  vi.mocked(paymentApi.get).mockResolvedValue({
    ...item,
    payer_id: "another-payer",
  });
  render(
    <TestBank overrides={{ data }}>
      <Payments />
    </TestBank>,
  );
  await screen.findByRole("dialog");
  expect(paymentApi.get).toHaveBeenCalledWith(item.id);
  expect(screen.queryByRole("button", { name: "Оплатить" })).toBeNull();
});

it("requires a second confirmation before declining", async () => {
  vi.mocked(paymentApi.action).mockResolvedValue({
    ...item,
    status: "declined",
  });
  render(
    <TestBank overrides={{ data }}>
      <Payments />
    </TestBank>,
  );
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: /Магазин Пчёлка → DemoOwner/ }),
  );
  await screen.findByRole("dialog");
  await user.click(screen.getByRole("button", { name: "Отклонить" }));
  expect(paymentApi.action).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Подтвердить" }));
  expect(paymentApi.action).toHaveBeenCalledWith(item.id, "decline");
});

it("shows pending requests on the dashboard and reuses payment confirmation", async () => {
  vi.mocked(paymentApi.list).mockResolvedValue({
    items: [item],
    next_offset: 5,
    total: 7,
  });
  vi.mocked(paymentApi.pay).mockImplementation(async () => {
    vi.mocked(paymentApi.list).mockResolvedValue({
      items: [],
      next_offset: null,
      total: 0,
    });
    return { ...item, status: "paid" };
  });
  render(
    <TestBank overrides={{ data }}>
      <PendingPayments />
    </TestBank>,
  );
  await screen.findByText("Ожидают оплаты");
  expect(screen.getByText("Всего: 7")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Показать все" })).toBeTruthy();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Оплатить" }));
  await screen.findByRole("dialog", { name: "Подтвердите оплату" });
  expect(paymentApi.pay).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: /Оплатить 150/ }));
  await screen.findByText("Статус запроса обновлён");
  expect(paymentApi.pay).toHaveBeenCalledWith(
    item.id,
    testAccount.id,
    expect.any(String),
  );
  expect(screen.queryByText("Ожидают оплаты")).toBeNull();
});

it("hides the dashboard block when there are no pending requests", async () => {
  vi.mocked(paymentApi.list).mockResolvedValue({
    items: [],
    next_offset: null,
    total: 0,
  });
  const view = render(
    <TestBank overrides={{ data }}>
      <PendingPayments />
    </TestBank>,
  );
  await vi.waitFor(() => expect(paymentApi.list).toHaveBeenCalled());
  expect(view.container.textContent).toBe("");
});

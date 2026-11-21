// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Transfer } from "./Transfer";
import { Administration } from "./Administration";
import { api } from "../api";
import {
  TestBank,
  testAccount,
  testQuote,
  testRecipient,
} from "../test-support";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      searchRecipients: vi.fn(),
      prepareTransfer: vi.fn(),
      confirmTransfer: vi.fn(),
      savedRecipients: vi.fn(),
      recentRecipients: vi.fn(),
      quote: vi.fn(),
    },
  };
});
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.mocked(api.savedRecipients).mockResolvedValue([]);
  vi.mocked(api.recentRecipients).mockResolvedValue([]);
  vi.mocked(api.searchRecipients).mockResolvedValue({ items: [testRecipient] });
  vi.mocked(api.prepareTransfer).mockResolvedValue(testQuote);
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

async function fill(amount = "25,50") {
  const user = userEvent.setup();
  await user.type(
    screen.getByRole("combobox", { name: "Кому перевести" }),
    "DemoPlayer",
  );
  const results = await screen.findByRole("listbox", {
    name: "Найденные получатели",
  });
  await user.click(within(results).getByRole("option"));
  await user.type(screen.getByLabelText("Сумма перевода"), amount);
  return user;
}
describe("User transfer confirmations", () => {
  it("prepares a recipient-bound quote but never sends funds before confirmation", async () => {
    render(
      <TestBank>
        <Transfer />
      </TestBank>,
    );
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Продолжить" }));
    await screen.findByRole("button", { name: "Подтвердить перевод" });
    expect(api.prepareTransfer).toHaveBeenCalledWith({
      recipient: { kind: "user", value: testRecipient.id },
      source_account_id: testAccount.id,
      amount: "25.50",
      reason: "",
    });
    expect(api.confirmTransfer).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("From account UUID")).toBeNull();
  });
  it("retries exactly the same server confirmation after an uncertain response", async () => {
    vi.mocked(api.confirmTransfer)
      .mockRejectedValueOnce(new TypeError("network error"))
      .mockResolvedValueOnce({
        id: "operation-1",
        type: "transfer",
        status: "completed",
        source_account_id: testAccount.id,
        destination_account_id: testRecipient.account_id,
        amount: testQuote.amount,
        fee: testQuote.fee,
        created_at: "2026-10-21T12:00:00Z",
        completed_at: "2026-10-21T12:00:00Z",
        sender: "DemoOwner",
        recipient: "DemoPlayer",
        direction: "outgoing",
      });
    render(
      <TestBank>
        <Transfer />
      </TestBank>,
    );
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Продолжить" }));
    await user.click(
      await screen.findByRole("button", { name: "Подтвердить перевод" }),
    );
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "Отмена" })).toBeNull();
    await user.click(
      screen.getByRole("button", { name: "Проверить результат / повторить" }),
    );
    await screen.findByText("Операция выполнена");
    expect(api.confirmTransfer).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.confirmTransfer).mock.calls[0][0]).toEqual(
      vi.mocked(api.confirmTransfer).mock.calls[1][0],
    );
    expect(
      sessionStorage.getItem("horizon.pending." + testAccount.owner_id),
    ).toBeNull();
  });
  it("rejects scientific notation before requesting a quote", async () => {
    render(
      <TestBank>
        <Transfer />
      </TestBank>,
    );
    const user = await fill("1e3");
    await user.click(screen.getByRole("button", { name: "Продолжить" }));
    expect(screen.getByRole("alert").textContent).toContain(
      "положительную сумму",
    );
    expect(api.prepareTransfer).not.toHaveBeenCalled();
  });
  it("restores a pending confirmation without automatically executing it", async () => {
    sessionStorage.setItem(
      "horizon.pending." + testAccount.owner_id,
      JSON.stringify({ id: testQuote.id, submitted: true }),
    );
    vi.mocked(api.quote).mockResolvedValue(testQuote);
    render(
      <TestBank>
        <Transfer />
      </TestBank>,
    );
    await screen.findByRole("button", {
      name: "Проверить результат / повторить",
    });
    expect(api.quote).toHaveBeenCalledWith(testQuote.id);
    expect(api.confirmTransfer).not.toHaveBeenCalled();
  });
  it("does not offer financial admin forms without explicit permissions", () => {
    render(
      <TestBank>
        <Administration permissions={[]} />
      </TestBank>,
    );
    expect(
      screen.queryByRole("button", { name: "Подтвердить выпуск" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Подтвердить возврат" }),
    ).toBeNull();
  });
});
